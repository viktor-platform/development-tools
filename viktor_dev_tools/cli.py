"""This is the main CLI module that contains all the commands and subcommands for the development tools

Note the use of absoluting importing `from viktor_dev_tools.` instead of the relative `from .tools`. This is to ensure
this package works both when executing as pip installed package, as well as running `python cli.py`.
"""
import subprocess
from collections import OrderedDict
from typing import Iterable
from typing import List

import click

from viktor_dev_tools.tools.subdomain import get_consolidated_login_details
from viktor_dev_tools.tools.subdomain import get_domain

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

option_username = click.option(
    "--username",
    "-u",
    help="Username for both subdomains, "
    "use this option when you want to reuse the credentials "
    "for both source an destination on the same domain",
)
option_source = click.option("--source", "-s", help="Source subdomain", prompt="Source VIKTOR sub-domain")
option_source_pwd = click.option("--source-pwd", "-sp", help="Source domain password")
option_source_token = click.option("--source-token", "-st", help="Source domain token ")
option_source_workspace = click.option(
    "--source-ws", "-sw", help="Source workspace id or name", prompt="Source workspace ID"
)
option_destination = click.option(
    "--destination",
    "-d",
    help="Destination subdomain",
)
option_destination_pwd = click.option("--destination-pwd", "-dp", help="Destination domain password")
option_destination_token = click.option("--destination-token", "-dt", help="Destination domain token ")
option_destination_workspace = click.option(
    "--destination-ws", "-dw", help="Destination workspace ID", prompt="Destination workspace ID"
)
option_destination_id = click.option("--destination-id", "-di", help="Destination parent entity id ")
option_source_id = click.option(
    "--source-ids", "-si", help="Source entity id (allows multiple)", prompt="Source entity ID", multiple=True
)


class OrderedGroup(click.Group):
    """Updated class to make the commands in helper docs in same order as defined below"""

    def __init__(self, name=None, commands=None, **attrs):
        super().__init__(name, commands, **attrs)
        self.commands = commands or OrderedDict()

    def list_commands(self, ctx) -> Iterable[str]:
        """Overwritten method to ensure ordered commands"""
        return self.commands


@click.group(cls=OrderedGroup, context_settings=CONTEXT_SETTINGS)
def cli():
    """This is the development tools command line interface.

    It contains the help explanation of all subcommands that are available.
    """


@cli.command()
@option_username
@option_source
@option_source_pwd
@option_destination
@option_destination_pwd
@option_source_token
@option_destination_token
@option_source_id
@option_destination_id
@click.option("--exclude-children", "-ec", is_flag=True, help="Exclude all children of source entity")
@option_source_workspace
@option_destination_workspace
def copy_entities(
    username: str,
    source: str,
    source_pwd: str,  # (default) will prompt for password unless `source_token` is supplied
    destination_pwd: str,  # (default) will prompt for password unless `destination_token` is supplied
    source_ws: str,
    destination_ws: str,
    destination: str,
    source_token: str,  # (Optional) if not set, will ask for pwd instead
    destination_token: str,  # (Optional) if not set, will ask for pwd instead
    source_ids: List[int],
    destination_id: int,
    exclude_children: bool,
) -> None:
    """Copy entities between domains.

    \b
    As a default, prompts user to fill in a password for subdomain, unless token is provided.
    If username is provided and source and destination are the same, password or token is re-used for destination.

    Example usage:

    $ copy-entities -s viktor  (prompts for password)

    $ copy-entities -s viktor -st Afj..sf  (uses bearer token)


    Allows copying multiple entity trees from the source, by specifying multiple source-ids. e.g. :

    $ copy-entities <other options> -si 922 -si 1032 -si 124

    """
    if not destination:
        destination = source

    source_pwd, source_token, destination_pwd, destination_token = get_consolidated_login_details(
        username, source, source_pwd, source_token, destination, destination_pwd, destination_token
    )
    source_domain = get_domain(source, username, source_pwd, source_token, source_ws)
    destination_domain = get_domain(destination, username, destination_pwd, destination_token, destination_ws)

    entity_type_mapping = source_domain.get_entity_type_mapping(destination_domain)

    for source_id in source_ids:
        entity_tree = source_domain.get_entity_tree(parent_id=source_id, exclude_children=exclude_children)
        destination_domain.post_entity_tree(entity_tree, entity_type_mapping, parent_id=destination_id)


@cli.command()
@option_username
@option_source
@option_source_pwd
@option_source_token
@option_source_workspace
@click.option("--destination", "-d", help="Destination path", prompt="Destination path")
@click.option(
    "--entity-type-names", "-etn", help="Entity type name (allows multiple)", prompt="Entity type name", multiple=True
)
@click.option("--include-revisions", "-rev", is_flag=True, help="Include all revisions of all entities Default: True")
def download_entities(
    username: str,
    source: str,
    source_pwd: str,
    source_token: str,
    destination: str,
    source_ws: str,
    entity_type_names: List[str],
    include_revisions: bool,
) -> None:
    """Download entities from domains.

    Download entities from source to destination on local filesystem, by entity_type.

    Example usage:

    $ download-entities -s geo-tools -d <path/on/computer> -u <username> -etn 'CPT File' -rev

    Allows copying multiple entities of multiple types from the source, by specifying multiple source-ids. e.g. :

    $ copy-entities <other options> -etn Section -etn Project -etn 'CPT File'

    """
    source_domain = get_domain(source, username, source_pwd, source_token, source_ws)
    source_domain.download_entities_of_type_to_local_folder(
        destination, entity_type_names=entity_type_names, include_revisions=include_revisions
    )


@cli.command()
@option_username
@option_source
@option_source_pwd
@option_source_workspace
@option_source_token
@click.option("--destination", "-d", help="Destination path", prompt="Destination path")
@click.option("--filename", "-f", help="Database filename (stored as json type)", prompt="Database filename")
@click.option("--apply", "-a", help="Apply a stashed database", is_flag=True)
def stash_database(
    username: str,
    source: str,
    source_pwd: str,
    source_ws: str,
    source_token: str,
    destination: str,
    filename: str,
    apply: bool,
) -> None:
    """Stashes the database from some domain, and applies it to some domain.

    You can only stash the database and apply the database if the amount of root entities in the manifest is still the
    same.

    By running this function without --apply, your database will be downloaded to some path specified by --destination
    and --filename.

    \b
    By running this function with --apply, the following will happen to your database:
        - Root entities will be replaced with their counterparts from the stashed database
        - All children from any root entity will be deleted
        - All children from the stashed database will be uploaded
    The database used for uploading comes from a path specified by --destination and --filename.

    Example usage:

    \b
    $ stash-database -u <username> -s <subdomain> -d <path/on/computer> -f dev-environment.json -sw 1
    $ stash-database -u <username> -s <subdomain> -d <path/on/computer> -f dev-environment.json -sw 1 --apply
    """
    # source domain when stashing, destination domain when applying
    domain = get_domain(source, username, source_pwd, source_token, source_ws)
    if apply:
        domain.upload_database_from_local_folder(source_folder=destination, filename=filename)
    else:
        domain.download_database_to_local_folder(destination, filename)


@cli.command()
def upgrade() -> None:
    """Upgrade the cli dependencies."""
    pip_install_command = ["pip", "install", "-e", ".", "--upgrade"]
    subprocess.run(pip_install_command, check=True)


if __name__ == "__main__":
    cli()
