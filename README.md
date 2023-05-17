# Multi GH Action Runner

For "reasons" (bad database design?) Github doesn't allow reusing the same self-hosted runner in multiple _personal_ repositories. (You can do it using an organization but not everybody has an organization). 
Similarly, if you have a powerful machine capable of running multiple jobs at the same time, setting up multiple runners and running them even for one repo is a pain.

There are various big solutions to this problem meant to work in an enterprise setup in AWS and such but these are an overkill for a simple developer who just wants to run actions on his own host.

This tool is an attempt to alleviate this problem. It orchestrates creation and running of multiple Github self-hosted runners driven by a config file. You can configure multiple runners per-repo for multiple repos and start and stop them all with a single command. The tool sets up new repos and cleans up no longer configured ones automatically at startup. 

There is no attempt to do dynamic provisioning based on web hooks. If you need something like that, perhaps a big enterprise solution is a better fir for your scenario.

This tool has been tested on macOS where I needed it for. 
It should work on Linux as is (if not please report a bug!).
It **will not** work on Windows. Python process control primitives it uses do not work on Windows.

## Setting it up

### Pre-requisites

* Python
* Any prerequisites for self-hosted Github runner

### Configuration

* Clone this repo in a folder. This folder will be called `ROOT` in what follows. __All the shell command below are assumed to be executed from it__.
* Create various directories:
```bash
mkdir runners # this is where runners will live
mkdir downloads # any temporary downloads, such as runner setups will be stored there
mkdir logs # all the logs from the system and each runner will be organized here
```
* Set up Python virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip3 install -r requirements.txt
```
* Create JSON configuration file `settings.json`. The template is given below:
```js
{
    "token": "Github token to use goes here...",
    "repos": {
        "name-of-a-repo": { "count": N, "namePrefix": "my-runner", "labels": ["my-label", ...]}, 
        ...
    },
    "extraEnv": {
        "SOME_ENVIRONMENT_VARIABLE": "its value"
    }
}
```
  * The Github token should be powerful enough to create/modify/delete self-hosted runners
  * `N` is the number of runners to assign to a repo named `name-of-a-repo`
  * Prefix will be used to name the runners like this `runner-1`, `runner-2`, ... `runner-N`
  * Labels are the extra labels to assign to the runner.
  * `extraEnv` are additional environment variables to give to each runner, if desired. You can use Python placeholders `{ANOTHER_VAR}` to reference other pre-existing environment vars there. Double `{{` and `}}` if you need these symbols in value (the usual Python `.format()` stuff)

That's it. The tool should now be ready to run manually

## Running manually

You can run the system manually either directly:

```bash
.venv/bin/python conductor.py
```

or from virtual environment

```bash
source .venv/bin/activate
./conductor.py
```

To stop it press `Ctrl-C`

The main script output will go to console. Output from each runner setup and execution can be found under `ROOT/logs`. 

## Setting up macOS `launchd` daemon

* Use `io.github.gershnik.gh-conductor.plist` as a template. 
* Replace:
    * `{MYPATH}` with the `ROOT`
    * `{USER}` with the username you want to run under (cannot be root)
    * `{GROUP}` with the user's primary group
* Put it under `/Library/LaunchDaemons`
* The usual stuff:
```bash
sudo launchctl load -w /Library/LaunchDaemons/io.github.gershnik.gh-conductor.plist`
sudo launchctl start io.github.gershnik.gh-conductor
sudo launchctl stop io.github.gershnik.gh-conductor
```

The daemon output will go into `ROOT/logs/conductor.log`

## Setting up Linux `systemd` daemon

TBD




