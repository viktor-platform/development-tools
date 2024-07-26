"""This is the main CLI module that contains all the commands and subcommands for the development tools

Note the use of absoluting importing `from viktor_dev_tools.` instead of the relative `from .tools`. This is to ensure
this package works both when executing as pip installed package, as well as running `python cli.py`.
"""
import os
import subprocess
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from typing import Iterable
from typing import List

import click
import requests

from viktor_dev_tools.tools.subdomain import ViktorUserDict, ViktorEnvironment, get_entity_tree, post_entity_tree, \
    get_workspace_id

from viktor import api_v1 as vk

STASH_PATH = Path.home() / '.viktor_dev_tools' / 'stash'
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
    "--source-ws", "-sw", help="Source workspaces id or name", prompt="Source workspaces ID"
)
option_source_id = click.option(
    "--source-id", "-si", help="Source entity id (allows multiple)", prompt="Source entity ID", multiple=True
)
option_destination = click.option(
    "--destination",
    "-d",
    help="Destination subdomain",
)
option_destination_pwd = click.option("--destination-pwd", "-dp", help="Destination domain password")
option_destination_token = click.option("--destination-token", "-dt", help="Destination domain token ")
option_destination_workspace = click.option(
    "--destination-ws", "-dw", help="Destination workspaces ID", prompt="Destination workspaces ID"
)
option_destination_id = click.option("--destination-id", "-di", help="Destination parent entity id ")


def require_credentials(obj):
    if not obj.env:
        obj.env = click.prompt('VIKTOR_ENV not set. Please provide your VIKTOR Environment domain, including `.viktor.ai`')
    while not obj.pat:
        obj.pat = click.prompt('VIKTOR_PAT not set. Please provide your VIKTOR Personal Access Token for your environment. (input is hidden)', hide_input=True)
        if not obj.pat.startswith("vktrpat_"):
            click.echo("Personal Access Token has invalid format")
            obj.pat = None


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


@cli.group(cls=OrderedGroup, context_settings=CONTEXT_SETTINGS)
@click.option('--env', envvar='VIKTOR_ENV', help="Your VIKTOR environment name, e.g. `company.viktor.ai`. Can also be set as env var: VIKTOR_ENV")
@click.option('--pat', envvar='VIKTOR_PAT', help="Your VIKTOR personal access token, e.g. `vktrpat_xxxxxx`. Can also be set as env var: VIKTOR_PAT")
@click.pass_context
def entities(ctx, env, pat):
    """Commands related to entity data.

    \b
    Requires:
    - VIKTOR environment name
    - VIKTOR personal access token
    """
    ctx.obj = SimpleNamespace(env=env, pat=pat)


@entities.command()
@click.argument("src_workspace")
@click.argument("src_entity", type=click.INT)
@click.argument("dest_workspace")
@click.argument("dest_entity", type=click.INT)
@click.option("--exclude-children", "-ec", is_flag=True, help="Exclude all children of source entity")
@click.pass_obj
def copy(
    obj,
    src_workspace: int,
    src_entity: int,
    dest_workspace: int,
    dest_entity: int,
    exclude_children: bool,
) -> None:
    """Copy entities (recursively) between workspaces

    \b
    Example usage:

    (specifying both workspace and entity ids):

    $ dev-cli entities copy 12 1032 22 12341

    (specifying workspace names and entity ids):

    $ dev-cli entities copy some-workspace 1032 development 12341
    """
    require_credentials(obj)
    api = vk.API(token=obj.pat, environment=obj.env)

    src_workspace = get_workspace_id(api, src_workspace)
    dest_workspace = get_workspace_id(api, dest_workspace)

    source_entities, parent_relations = get_entity_tree(
        api=api, workspace_id=src_workspace, entity_id=src_entity, recursive=not exclude_children
    )
    post_entity_tree(
        api=api,
        destination_workspace_id=dest_workspace,
        destination_parent_id=dest_entity,
        source_entities=source_entities,
        source_relations=parent_relations,
    )


@cli.group(cls=OrderedGroup, context_settings=CONTEXT_SETTINGS)
@click.option('--env', envvar='VIKTOR_ENV', help="Your VIKTOR environment name, e.g. `company.viktor.ai`. Can also be set as env var: VIKTOR_ENV")
@click.option('--pat', envvar='VIKTOR_PAT', help="Your VIKTOR personal access token, e.g. `vktrpat_xxxxxx`. Can also be set as env var: VIKTOR_PAT")
@click.pass_context
def users(ctx, env, pat):
    """Commands related to user data.

    \b
    Requires:
    - VIKTOR environment name
    - VIKTOR personal access token
    """
    ctx.obj = SimpleNamespace(env=env, pat=pat)


@users.command()
@click.option(
    "--filepath",
    "-f",
    help="The file path of the csv with the list of users.\n"
         "The csv file contain the following columns:\n"
         "- first_name\n"
         "- last_name\n"
         "- email\n"
         "- job_title (Optional)",
    prompt="File path"
)
@click.pass_obj
def add(obj, filepath: str):
    """Add users in bulk to the domain.

    \b
    As a default, prompts user to fill in a password for subdomain, unless token is provided.
    If username is provided and source and destination are the same, password or token is re-used for destination.

    Example usage:

    $ add-users -u <username> -s <subdomain> -f <path/on/computer>

    """
    require_credentials(obj)
    source_domain = ViktorEnvironment(environment=ctx.env, personal_access_token=ctx.pat, workspace="1")

    users_df = pd.read_csv(filepath)
    for key in ["first_name", "last_name", "email"]:
        if key not in users_df.columns.values:
            ValueError(f'CSV does not have a column with label "{key}"')
    users_list = users_df.to_dict("records")
    for user_dict in users_list:
        user = ViktorUserDict(
            first_name=user_dict["first_name"],
            last_name=user_dict["last_name"],
            name=f"{user_dict['first_name']} {user_dict['last_name']}",
            email=user_dict["email"],
            job_title=user_dict.get("job_title", ""),
            is_dev=True,
        )
        try:
            source_domain.add_user(user)
        except requests.exceptions.HTTPError as e:
            click.echo(e)
            click.echo(f"Failed to add user: {user_dict['first_name']} {user_dict['last_name']}")
            click.echo()
            click.echo("Continue with next user...")


@entities.command()
@option_source_workspace
@click.option("--destination", "-d", help="Destination path", prompt="Destination path")
@click.option(
    "--entity-type-names", "-etn", help="Entity type name (allows multiple)", prompt="Entity type name", multiple=True
)
@click.option("--include-revisions", "-rev", is_flag=True, help="Include all revisions of all entities Default: True")
@click.pass_obj
def download(
    obj,
    source_ws: str,
    destination: str,
    entity_type_names: List[str],
    include_revisions: bool,
) -> None:
    """Download entities from a workspace.

    Download entities from source to destination on local filesystem, by entity_type.

    Example usage:

    $ download-entities -s geo-tools -d <path/on/computer> -u <username> -etn 'CPT File' -rev

    Allows copying multiple entities of multiple types from the source, by specifying multiple source-ids. e.g. :

    $ copy-entities <other options> -etn Section -etn Project -etn 'CPT File'

    """
    require_credentials(obj)
    source_domain = ViktorEnvironment(environment=obj.env, personal_access_token=obj.pat, workspace=source_ws)
    source_domain.download_entities_of_type_to_local_folder(
        destination, entity_type_names=entity_type_names, include_revisions=include_revisions
    )


@entities.command()
@option_source_workspace
@click.option("--destination", "-d", help="Destination path", prompt="Destination path")
@click.option("--filename", "-f", help="Database filename (stored as json type)", prompt="Database filename")
@click.option("--apply", "-a", help="Apply a stashed database", is_flag=True)
@click.pass_obj
def stash(
    obj,
    source_ws: str,
    destination: str,
    filename: str,
    apply: bool,
) -> None:
    """Stashes the complete entity structure of a workspaces.

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
    require_credentials(obj)
    # source domain when stashing, destination domain when applying
    domain = ViktorEnvironment(environment=obj.env, personal_access_token=obj.pat, workspace=source_ws)
    if apply:
        domain.upload_database_from_local_folder(source_folder=destination, filename=filename)
    else:
        domain.download_database_to_local_folder(destination, filename)


@cli.command()
def upgrade() -> None:
    """Upgrade the cli dependencies."""
    pip_install_command = ["pip", "install", "-e", ".", "--upgrade"]
    subprocess.run(pip_install_command, check=True)


@cli.command()
@click.pass_obj
def show_creds(obj) -> None:
    """Check the creds that are configured"""
    env = os.environ.get("VIKTOR_ENV") or "Not configured!"
    _pat = os.environ.get("VIKTOR_PAT")
    pat_masked = _pat[:16] + "*" * 48 if _pat else "Not configured!"
    click.echo(f"VIKTOR_ENV = {env}")
    click.echo(f"VIKTOR_PAT = {pat_masked}")


if __name__ == "__main__":
    cli()
