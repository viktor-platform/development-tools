# READ THIS FIRST
SERIOUS, IRREVERSIBLE DAMAGE CAN BE DONE WITH THIS TOOL TO YOUR ENVIRONMENT AND DATABASES.
USE FOR EXPERT DEVELOPERS ONLY. USE AT OWN RISK. 

# Viktor Development Tools
This repository is a collection of tools to make life easier for VIKTOR-developers.

# Installation
We have created a pip-installable package that will download the necessary dependencies for you. This will also create a
CLI executable (`dev-cli`) that can be called from any directory.

The current implementation of the cli has been tested on both Windows and Linux. Tested and working for Python versions 3.8 up to 3.11.

> Note:
To prevent dependency conflicts, one can consider pip installing the package inside a virtual environment. For more 
information on the set-up with a virtual env.

## Pip-installing from GitHub
Run the following command to pip install directly from the repository master branch (stable release):
```
pip install git+ssh://git@gitlab.viktor.ai/viktor-company/viktor-development-tools.git@master
```

# Using the Development Tools
To use the tools/scripts in this repository, you can simply call (from any folder):
```bash
dev-cli -h
```
which looks something like this:
```commandline
Usage: dev-cli [OPTIONS] COMMAND [ARGS]...

  This is the development tools command line interface.

  It contains the help explanation of all subcommands that are available.

Options:
  -h, --help  Show this message and exit.

Commands:
  copy-entities      Copy entities between domains.
  download-entities  Download entities from domains.
  stash-database     Stashes the database from some domain, and applies it...
  upgrade            Upgrade the cli dependencies.
```

The `-h` option should provide enough help for each of the available commands.

> Note: if you have installed using a virtual environment, don't forget to activate and/or deactivate the virtual env.
