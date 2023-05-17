#! /usr/bin/env python3

import sys
import os
import json
import shutil
import tarfile
import subprocess
import signal
import logging

import urllib.request

from pathlib import Path
from github.Requester import Requester
from github.Consts import (
    DEFAULT_BASE_URL as GHA_DEFAULT_BASE_URL, 
    DEFAULT_TIMEOUT as GHA_DEFAULT_TIMEOUT,
    DEFAULT_PER_PAGE as GHA_DEFAULT_PER_PAGE
)
from datetime import datetime
from dataclasses import dataclass
from setproctitle import setproctitle

from typing import List, Dict

@dataclass
class Token:
    value: str
    expiresAt: datetime


setproctitle('Github Runners')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

MYPATH = Path(__file__).parent
LogDir = MYPATH / 'logs'
DownloadDir = MYPATH / 'downloads'

logging.info('Killing all active runners')
subprocess.run(['pkill', '-INT', 'Runner.Listener'], stdin=None)


with open(MYPATH / 'settings.json') as settingsFile:
    settings = json.load(settingsFile)

logging.info("Connecting to Github API")
rq = Requester(settings["token"],
               password=None,
               jwt=None,
               app_auth=None,
               base_url=GHA_DEFAULT_BASE_URL,
               timeout=GHA_DEFAULT_TIMEOUT,
               user_agent="PyGithub/Python",
               per_page=GHA_DEFAULT_PER_PAGE,
               verify=True,
               retry=None,
               pool_size=None)


def fetchLatestPackage(runnerPlatform: str, downloadDir: Path):
    _, data = rq.requestJsonAndCheck("GET", '/repos/actions/runner/releases/latest')
    latestVersionLabel = data['tag_name']
    latestVersion = latestVersionLabel[1:]
    runnerFile=f"actions-runner-{runnerPlatform}-{latestVersion}.tar.gz"
    downloadDir.mkdir(exist_ok=True)
    packagePath = downloadDir / runnerFile
    if not packagePath.exists():
        url = f"https://github.com/actions/runner/releases/download/{latestVersionLabel}/{runnerFile}"
        urllib.request.urlretrieve(url, packagePath)
    return packagePath

PackagePath = fetchLatestPackage('osx-x64', DownloadDir)

ConfigTokens = {}

def tokenForRepo(repo: str):
    global ConfigTokens
    token = ConfigTokens.get(repo)
    now = datetime.now().astimezone()
    if not token or token.expiresAt <= now:
        _, data = rq.requestJsonAndCheck("POST", f"/repos/gershnik/{repo}/actions/runners/registration-token")
        token = Token(data['token'], datetime.fromisoformat(data['expires_at']))
        ConfigTokens[repo] = token
    return token


def configureRunner(repo: str, name: str, labels: List[str], runnerPath: Path):
    runnerPath.mkdir(parents=True)
    logging.info(f'Unpacking runner package into {runnerPath}')
    with tarfile.open(PackagePath) as package:
        package.extractall(runnerPath)

    repoLogDir = LogDir / repo
    repoLogDir.mkdir(parents=True, exist_ok=True)

    token = tokenForRepo(repo)
    logging.info(f'Configuring runner {name} for {repo} at {runnerPath}')
    with open(repoLogDir / f'config-{name}.log', 'w') as logFile:
        subprocess.run(['./config.sh', '--unattended', 
                        '--url', f'https://github.com/gershnik/{repo}',
                        '--token', token.value,
                        '--name', name,
                        '--labels', ','.join(labels),
                        '--replace'], cwd=runnerPath, check=True, stdout=logFile, stderr=logFile, stdin=None)

def deleteGHRunner(repo, runner):
    runnerId = runner['id']
    rq.requestJsonAndCheck("DELETE", f"/repos/gershnik/{repo}/actions/runners/{runnerId}")


def configureRunners() -> Dict[str, any]:
    logging.info("Configuring runners")
    runnersByRepo = {}
    for repo, config in settings['repos'].items():
        logging.info(f"Processing repo {repo}")
        
        _, data = rq.requestJsonAndCheck("GET", f"/repos/gershnik/{repo}/actions/runners")

        oldConfiguredRunners = {}
        for runner in data["runners"]:
            if runner["busy"] == True:
                raise RuntimeError(f'Runner {runner["name"]} is busy, cannot continue')
            oldConfiguredRunners[runner["name"]] = runner

        newConfiguredRunners = []
        for idx in range(config['count']):
            name = f'{config["namePrefix"]}-{idx + 1}'
            runner = oldConfiguredRunners.get(name)
            if runner:
                del oldConfiguredRunners[name]
                oldLabels = [label['name'] for label in runner['labels']] 
            else:
                oldLabels = []
                
            newConfiguredRunners.append(name)
            newLabels = config['labels']
            runnerPath = MYPATH / f'runners/{repo}/{name}' 

            if runner and runnerPath.exists():
                if all(x in oldLabels for x in newLabels): 
                    logging.info(f"Runner {name} already configured, reusing")
                else:
                    logging.info(f"Runner {name} already configured but labels don't match, removing directory and configuring")
                    shutil.rmtree(runnerPath)
                    configureRunner(repo, name, newLabels, runnerPath)
            elif runner:
                logging.warning(f"Runner {name} is configured on Github but has no directory, configuring and replacing")
                #deleteGHRunner(repo, runner)
                configureRunner(repo, name, newLabels, runnerPath)
            elif runnerPath.exists():
                logging.warning(f"Runner {name} has a directory but not Github config, removing directory and configuring")
                shutil.rmtree(runnerPath)
                configureRunner(repo, name, newLabels, runnerPath)
            else:
                logging.info(f"Runner {name} is new, configuring")
                configureRunner(repo, name, newLabels, runnerPath)
            

        for name, runner in oldConfiguredRunners.items():
            if not name.startswith(config["namePrefix"]):
                logging.info(f'Ignoring existing runner {name} - not ours')
                continue

            logging.info(f"Runner {name} is configured on Github but no longer in our configuration, removing from Github")
            deleteGHRunner(repo, runner)
            runnerPath = MYPATH / f'runners/{repo}/{name}'
            if runnerPath.exists():
                logging.info(f"Removing obsolete {runnerPath}")
                shutil.rmtree(runnerPath)

        for item in list((MYPATH / f'runners/{repo}').iterdir()):
            if item.is_dir():
                if not (item.name in newConfiguredRunners):
                    logging.info(f"Removing orphaned {item}")
                    shutil.rmtree(item)

        runnersByRepo[repo] = newConfiguredRunners

    return runnersByRepo


def startRunner(repo: str, name: str, repoLogDir: Path):
    runnerPath = MYPATH / f'runners/{repo}/{name}'
    env = os.environ.copy()
    for key, value in settings.get("extraEnv", {}).items():
        env[key] = value.format(**os.environ)
    try:
        logging.info(f"Starting {repo} {name}...")
        childId = os.posix_spawn(
                    runnerPath/ 'run.sh', 
                    [runnerPath/ 'run.sh'], 
                    env, 
                    file_actions=[
                        (os.POSIX_SPAWN_CLOSE, sys.stdin.fileno()),
                        (os.POSIX_SPAWN_OPEN, sys.stdout.fileno(), 
                                repoLogDir / f'{name}.log', os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o660),
                        (os.POSIX_SPAWN_DUP2, sys.stdout.fileno(), sys.stderr.fileno())
                    ],
                    setpgroup = 0,
                    setsigdef = (signal.SIGINT, signal.SIGTERM))
        logging.info(f"Started, process ID {childId}")
        return childId
    except Exception as ex:
        logging.exception(ex)
        return None
            
runnersByRepo = configureRunners()


childProcesses = {}
childrenKilled = False


def killAllChildren():
    global childrenKilled
    if not childrenKilled:
        signo = signal.SIGINT
        signame = signal.strsignal(signo) or str(signo)
        for child in childProcesses.keys():
            logging.info(f'Sending {signame} to {child}')
            os.kill(-child, signo)
        childrenKilled = True


def handleSignal(signo, frame):
    signame = signal.strsignal(signo) or str(signo)
    logging.info(f"Received signal {signame}")
    killAllChildren()

signal.signal(signal.SIGINT, handleSignal)
signal.signal(signal.SIGTERM, handleSignal)

logging.info("Starting runners")
for repo, runners in runnersByRepo.items():
    repoLogDir = LogDir / repo
    repoLogDir.mkdir(parents=True, exist_ok=True)
    for name in runners:
        childId = startRunner(repo, name, repoLogDir)
        if childId is None:
            killAllChildren()
            break
        childProcesses[childId] = (repo, name)


logging.info("Waiting for runners")
while len(childProcesses) > 0:
    pid, status = os.waitpid(-1, os.WUNTRACED)
    repo, name = childProcesses[pid]
    del childProcesses[pid]
    if os.WIFSIGNALED(status):
        termsig = os.WTERMSIG(status)
        signame = signal.strsignal(termsig) or str(termsig)
        logging.info(f"Runner for {repo} {name} was killed by signal {signame} - exiting")
        killAllChildren()
    else:
        exitCode = os.WEXITSTATUS(status)
        logging.info(f"Runner for {repo} {name} exited with code {exitCode} - exiting")
        killAllChildren()


