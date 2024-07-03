"""This module contains a class representation and all related functions for a Viktor Sub-domain"""
import json
import sys
from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
from typing import TypedDict
from typing import Union

import click
import requests

from viktor import api_v1 as vk

from viktor_dev_tools.tools.config import CLIENT_ID
from viktor_dev_tools.tools.config import CLIENT_ID_SSO
from viktor_dev_tools.tools.helper_functions import add_field_names_referring_to_entities_to_container
from viktor_dev_tools.tools.helper_functions import update_id_on_entity_fields
from viktor_dev_tools.tools.helper_functions import validate_root_entities_compatibility




# ============================== Authentication related classes and constants ============================== #

_STANDARD_HEADERS = {"Content-Type": "application/json"}


# ============================== Entity dictionary related functions and classes ============================== #
class EntityDict(TypedDict, total=False):
    """TypedDict that represent an entity dictionary"""

    id: int
    name: str
    entity_type: int
    properties: Dict
    children: list
    parent_entity_type: int


class ViktorUserDict(TypedDict, total=False):
    """TypedDict that represent a user dictionary"""

    id: int
    name: str
    first_name: str
    last_name: str
    username: str
    email: str
    job_title: str
    email: str
    is_dev: bool
    is_env_admin: bool
    is_external: bool


def _repr_entities(entities: List[EntityDict]) -> str:
    """Returns a string representation of a list of entities.

    Looks like:
     - <entity_name_1>: 1
     - <entity_name_2>: 5
     - <entity_name_3>: 8
    """
    return " - " + "\n - ".join([f"{entity['name']}: {entity['id']}" for entity in entities])


def _get_element_count_of_tree(children: List[dict], size: int = 1) -> int:
    """Recursively count all children in the tree"""
    for child in children:
        size += _get_element_count_of_tree(child["children"])
    return size


# ============================== S3 related functions ============================== #
def get_file_content_from_s3(entity: EntityDict) -> Optional[bytes]:
    """If entity has a filename property, download the file_content from the temporary download url"""
    temp_download_url = entity["properties"].get("filename", None)
    if temp_download_url:
        file_download = requests.get(temp_download_url, timeout=60)
        return file_download.content
    return None


# ================================================================================== #


def get_consolidated_login_details(
    username: str,
    source: str,
    source_pwd: str,
    source_token: str,
    destination: str,
    destination_pwd: str,
    destination_token: str,
):
    """Checks if the source and destination domain are identical. If so, re-uses username and password or token."""
    if source_token is not None and source == destination:
        destination_token = destination_token or source_token

    if username is not None and source == destination:
        source_pwd = source_pwd or click.prompt(f"Password for {source}", hide_input=True)
        destination_pwd = destination_pwd or source_pwd

    return source_pwd, source_token, destination_pwd, destination_token


def get_domain(subdomain, username, pwd, token, workspace: str, refresh_token=None):
    """Create a subdomain either from SSO or username and password"""
    if token:
        return ViktorSubDomain.from_token(
            sub_domain=subdomain, access_token=token, refresh_token=refresh_token, workspace=workspace
        )
    username = username or click.prompt(f"Username for {subdomain}")
    password = pwd or click.prompt(f"Password for {subdomain}", hide_input=True)
    return ViktorSubDomain.from_login(sub_domain=subdomain, username=username, password=password, workspace=workspace)


def get_entity_type_mapping_from_entity_types(
    source_entity_types: List[Dict], destination_entity_types: List[Dict]
) -> Dict[int, int]:
    """Maps the id's of the entity types from source to destination, based on entity type name.

    The format used as input for this method is the output from ViktorSubDomain().get_entity_types()"""
    mapping_dict: Dict[int, int] = {}
    for destination_entity_type in destination_entity_types:
        for source_entity_type in source_entity_types:
            if source_entity_type["class_name"] == destination_entity_type["class_name"]:
                mapping_dict.update({source_entity_type["id"]: destination_entity_type["id"]})
    return mapping_dict


class ExtendedAPI(vk.API):

    def get_entity_tree(self, workspace_id: int, entity_id: int):
        entities = {entity_id: self.get_entity(entity_id, workspace_id=workspace_id)}
        parent_relations = {entity_id: None}

        def get_entity_children_recursive(entity_):
            children = entity_.children()
            for child in children:
                parent_relations[child.id] = entity_.id
                entities[child.id] = child
                get_entity_children_recursive(child)

        get_entity_children_recursive(entities[entity_id])

        return entities, parent_relations

    def post_entity_tree(
            self, workspace_id: int, parent_id: int, entities: dict[int, vk.Entity], parent_relations: dict[int, int],
            dry_run: bool = True
    ) -> None:
        parent_entity = self.get_entity(parent_id, workspace_id=workspace_id)
        other_parent = next(child_id for child_id, parent_id_ in parent_relations.items() if parent_id_ is None)

        def post_children_recursive(entity_: vk.Entity, other_parent_id: int):
            children = [
                entities[child_id] for child_id, parent_id_ in parent_relations.items() if parent_id_ == other_parent_id
            ]
            for child in children:
                if not dry_run:
                    try:
                        params = child.last_saved_params
                    except AttributeError:
                        params = {}
                    new_child = entity_.create_child(
                        entity_type_name=child.entity_type.name,
                        name=child.name,
                        params=params,
                        workspace_id=workspace_id
                    )
                self._progressbar.update(1)
                post_children_recursive(new_child, child.id)

        progressbar_label = f'Posting entities to {workspace_id} {"(dry run)" if dry_run else ""}'
        with click.progressbar(length=len(entities) - 1, label=progressbar_label) as progressbar:
            self._progressbar = progressbar
            post_children_recursive(parent_entity, other_parent)


class ViktorSubDomain:
    """Class representation of a VIKTOR sub-domain.
    Handles the logging in and out when instantiating and destroying a ViktorSubDomain class.
    """

    _logged_in = False

    # ============================== All authentication related requests ============================== #
    def __init__(
        self,
        sub_domain: str,
        auth_details: dict,
        client_id: str,
        workspace: str,
        access_token: str = None,
        refresh_token: str = None,
    ):
        print(f"Logging in to {sub_domain}")
        self.name = sub_domain
        self.host = f"https://{sub_domain}.viktor.ai/api"
        self.client_id = client_id
        if not access_token:
            # Perform post request to '/o/token/' end-point to login
            response = requests.post(
                f"{self.host}/o/token/", data=json.dumps(auth_details), headers=_STANDARD_HEADERS, timeout=10
            )
            if not 200 <= response.status_code < 300:
                print(f"Provided credentials are not valid.\n{response.text}")
                sys.exit(1)
            self.access_token = response.json()["access_token"]
            self.refresh_token = response.json()["refresh_token"]
        else:
            self.access_token = access_token
            self.refresh_token = refresh_token

        self.workspace_id = self.get_workspace_id(workspace)
        self.workspace = f"/workspaces/{self.workspace_id}"
        print(f"Selecting workspace {self.workspace_id}")
        # Set empty parameters in init
        self._progressbar = None
        self._logged_in = True

    @property
    def headers(self) -> dict:
        """Retrieves headers based on current access token"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
        }

    def get_workspace_id(self, workspace_id_or_name: Union[str, int]) -> int:
        """Retrieves the workspace id based on given id or name"""
        if not isinstance(workspace_id_or_name, (str, int)):
            raise TypeError("Workspace should be of type int or str.")
        try:
            return int(workspace_id_or_name)
        except ValueError as exc:
            workspace_id_or_name = workspace_id_or_name.lower()
            workspaces_mapping = self.get_workspaces_mapping()
            if workspace_id_or_name.lower() not in workspaces_mapping.keys():
                available_workspaces = "\n - ".join(workspaces_mapping.keys())
                message = (
                    f"Requested workspace {workspace_id_or_name} was not found on subdomain. "
                    f"Available workspaces are: \n - {available_workspaces}"
                )
                raise click.ClickException(message) from exc
            return workspaces_mapping[workspace_id_or_name]

    def __del__(self):
        """Log out of the subdomain by revoking the access access_token"""
        if self._logged_in and self.client_id == CLIENT_ID:  # Do not logout SSO, since token already expires in 15 min.
            payload = {"client_id": self.client_id, "token": self.access_token}
            response = requests.post(
                f"{self.host}/o/revoke_token/", data=json.dumps(payload), headers=_STANDARD_HEADERS, timeout=10
            )
            if 200 <= response.status_code < 300:
                print(f"Successfully logged out ({self.name}) ")
            else:
                print(f"Unable to log out!  ({self.name})")
                sys.exit(1)

    def refresh_tokens(self) -> None:
        """Tokens for SSO expire within 900 seconds, so this function refreshes the tokens when it is expired"""
        payload = {"refresh_token": self.refresh_token, "client_id": self.client_id, "grant_type": "refresh_token"}
        response = requests.post(
            f"{self.host}/o/token/", data=json.dumps(payload), headers=_STANDARD_HEADERS, timeout=10
        )
        response_json = response.json()
        self.access_token = response_json["access_token"]
        self.refresh_token = response_json["refresh_token"]

    @classmethod
    def from_token(cls, sub_domain: str, access_token: str, refresh_token: str = None, workspace: str = "1"):
        """Class method to login with Bearer Token, useful for SSO environments"""
        return cls(sub_domain, {}, CLIENT_ID_SSO, access_token, refresh_token, workspace)

    @classmethod
    def from_login(cls, sub_domain: str, username: str, password: str, workspace: str) -> "ViktorSubDomain":
        """Class method to login with sub-domain, username and password"""
        if not username or not password:
            print("Provide both username and password.")
            sys.exit(1)

        auth_details = {"client_id": CLIENT_ID, "username": username, "password": password, "grant_type": "password"}
        return cls(sub_domain, auth_details, CLIENT_ID, workspace=workspace)

    def get_workspaces_mapping(self) -> Dict[str, int]:
        """Maps the workspace name (not case sensitive) to the workspace ID"""
        workspaces_list = self._get_request(path="/workspaces/", exclude_workspace=True)
        return {item["name"].lower(): int(item["id"]) for item in sorted(workspaces_list, key=lambda p: p["id"])}

    # ============================== Basic GET and POST requests ============================== #
    def _get_request(self, path: str, exclude_workspace: bool = False) -> Union[dict, List[dict]]:
        """Simple get request using the subdomain authentication"""
        if not path.startswith("/"):
            raise SyntaxError('URL should start with a "/"')
        response = requests.request(
            "GET", f"{self.host}{'' if exclude_workspace else self.workspace}{path}", headers=self.headers, timeout=10
        )
        if response.status_code == 401:
            self.refresh_tokens()
            response = requests.request(
                "GET",
                f"{self.host}{'' if exclude_workspace else self.workspace}{path}",
                headers=self.headers,
                timeout=10,
            )
        response.raise_for_status()
        return response.json()

    def _post_request(self, path: str, data: dict, exclude_workspace: bool = False) -> Union[dict, list, None]:
        """Simple post request using the subdomain authentication.

        Use exclude_workspace flag if the post request should be send to an URL that does not include a workspace.
        """
        if not path.startswith("/"):
            raise SyntaxError('URL should start with a "/"')
        response = requests.request(
            "POST",
            f"{self.host}{'' if exclude_workspace else self.workspace}{path}",
            data=json.dumps(data),
            headers=self.headers,
            timeout=10,
        )
        if response.status_code == 401:
            self.refresh_tokens()
            response = requests.request(
                "POST",
                f"{self.host}{'' if exclude_workspace else self.workspace}{path}",
                data=json.dumps(data),
                headers=self.headers,
                timeout=10,
            )
        response.raise_for_status()
        if response.text:  # A DELETE request has no returned text, so check if there is text
            return response.json()
        return None

    def _put_request(self, path: str, data: dict) -> Union[dict, list]:
        """Simple put request using the subdomain authentication"""
        if not path.startswith("/"):
            raise SyntaxError('URL should start with a "/"')
        response = requests.request(
            "PUT", f"{self.host}{self.workspace}{path}", data=json.dumps(data), headers=self.headers, timeout=30
        )
        response.raise_for_status()
        return response.json()

    def _delete_request(self, path: str) -> None:
        """Simple delete request"""
        if not path.startswith("/"):
            raise SyntaxError('URL should start with a "/"')
        response = requests.request("DELETE", f"{self.host}{self.workspace}{path}", headers=self.headers, timeout=30)
        response.raise_for_status()

    # ============================== All GET requests ============================== #
    def get_root_entities(self) -> List[EntityDict]:
        """Replacement of the entity().root_entities() method in the SDK"""
        return self._get_request("/entities/")

    def get_entity_types(self) -> List[dict]:
        """Replacement of the entity_types() method in the SDK"""
        return self._get_request("/entity_types/")

    def get_all_entities_of_entity_type(self, entity_type: int) -> List[EntityDict]:
        """Replacement of the entity_type(id).entities() method in the SDK"""
        return self._get_request(f"/entity_types/{entity_type}/entities/")

    def get_parents(self, entity_id: int) -> List[EntityDict]:
        """Replacement of the entity().parents() method in the SDK"""
        return self._get_request(f"/entities/{entity_id}/parents/")

    def get_entity(self, entity_id: int, recursive: bool = False) -> EntityDict:
        """Replacement of the entity().get() method in the SDK"""
        parents = self.get_parents(entity_id)
        entity = self._get_request(f"/entities/{entity_id}/")
        self._clean_up_entity(entity)

        # Save the parent entity type, which can be useful when posting the entity later on.
        entity.update({"parent_entity_type": parents[0]["entity_type"] if parents else None, "size": 1, "children": []})

        if recursive:
            entity.update({"children": self.get_children(entity["id"], recursive=recursive)})
            entity.update({"size": _get_element_count_of_tree([entity], size=0)})

        return entity

    def get_entity_revisions(self, entity_id: int) -> List[EntityDict]:
        """Gets a list of all revisions for a single entity"""
        return self._get_request(f"/entities/{entity_id}/revisions/")

    def get_children(self, parent_id: int, recursive: bool = False) -> Optional[List[Dict]]:
        """Replacement of the entity().children() method in the SDK

        In addition to the standard get children function, for each file entity the filename-property is replaced with
        the temporary_download_url from S3. This will allow the file to be downloaded, should it be needed later on.
        """
        children = self._get_request(f"/entities/{parent_id}/entities/")

        if not children:
            return []

        # Replace filename url with temporary download url and remove any references to entity id's
        for child in children:
            self._clean_up_entity(child)

        if recursive:
            return [
                {
                    "id": child["id"],
                    "entity_type": child["entity_type"],
                    "name": child["name"],
                    "properties": child["properties"],
                    "children": self.get_children(child["id"], recursive=recursive),
                }
                for child in children
            ]

        return children

    def get_entity_tree(self, parent_id: int = None, exclude_children: bool = False) -> EntityDict:
        """Iterates through the entity database using a recursive function.

        Prompts the user for the top level entity to start with and then goes down the entity tree.
        """
        parent_id = parent_id or int(input("Insert entity_id of desired entity: "))
        parent_entity = self.get_entity(parent_id)
        if exclude_children:
            print(f'Retrieving {parent_entity["name"]}')
            entity_tree = self.get_entity(parent_id, recursive=False)
            print("Successfully obtained entity!\n")
        else:
            if not parent_entity["parent_entity_type"]:
                click.secho(
                    "Note: Current entity is a Root entity. "
                    "A Root entity cannot be created in the destination, only updated.\n"
                    "Consider using the `copy-revision` command to update the Root entity params:\n"
                    f"`dev-cli copy-revision -s {self.name} -sw {self.workspace_id} -si {parent_id} ...`",
                    fg="bright_yellow",
                )
            else:
                print(f'Retrieving {parent_entity["name"]}.')
            print(f'Recursively retrieving all entities under {parent_entity["name"]}.')
            entity_tree = self.get_entity(parent_id, recursive=True)
            print("Successfully obtained all entities!\n")
        return entity_tree

    def get_entity_type_mapping(self, destination: "ViktorSubDomain") -> Dict[int, int]:
        """Maps the id's of the entity types from source to destination, based on entity type name"""
        source_entity_types = self.get_entity_types()
        mapping_dict = get_entity_type_mapping_from_entity_types(source_entity_types, destination.get_entity_types())
        if len(mapping_dict) < len(source_entity_types):
            click.confirm(
                f"Not all entity types in {self.name} match with {destination.name}.\n"
                f"Are you sure you are copying the entities to the correct destination?",
                abort=True,
                default=True,
            )
        return mapping_dict

    def get_all_users(self) -> List[ViktorUserDict]:
        """Retrieves all users in a subdomain"""
        return self._get_request("/users/")

    # ============================== All POST requests ============================== #
    def get_parametrization(self, entity_id: int) -> Dict:
        """Get the parametrization of the current entity. In this parametrization the field types can be found"""
        return self._post_request(f"/entities/{entity_id}/parametrization/", {})

    def upload_file(self, file_content: bytes, entity_type: int) -> str:
        """Uploads a file to S3 using the host authentication and returns the filename url"""
        # Upload the file to S3
        result = self._post_request(f"/entity_types/{entity_type}/upload/", data={})
        requests.post(result["url"], data=result["fields"], files={"file": file_content}, timeout=60)

        # Return the filename url which should be add the the file entity
        return result["fields"]["key"]

    def post_child(
        self,
        parent_id: int,
        entity_type: int,
        entity_dict: dict,
        file_content: bytes = None,
        dry_run: bool = False,
        old_to_new_ids_mapping: Optional[Dict] = None,
    ) -> EntityDict:
        """Replacement of the entity().post_child() method in the SDK

        Has additional option to include file_content, which creates the file entity and also
        uploads file_content to S3.

        If an old_to_new_ids_mapping is given as dict, the dict is updated with a
        mapping of the children {old_id: new_id}
        """
        if dry_run:
            return {"id": 0}

        if file_content:
            file_url = self.upload_file(file_content, entity_type)
            entity_dict["properties"].update({"filename": file_url})

        data = {"entity_type": entity_type, "name": entity_dict["name"], "properties": entity_dict["properties"]}
        response = self._post_request(f"/entities/{parent_id}/entities/", data)
        self._progressbar.update(1)
        if old_to_new_ids_mapping is not None:
            old_to_new_ids_mapping[entity_dict["id"]] = response["id"]

        return response

    def post_children(
        self,
        parent_id: int,
        children: List[dict],
        entity_type_mapping: dict,
        dry_run: bool = False,
        recursive: bool = False,
        old_to_new_ids_mapping: Optional[Dict] = None,
    ) -> None:
        """Creates the children under the top-level entity"""
        for child in children:
            try:
                new_entity_type_id = entity_type_mapping[child["entity_type"]]
                created_child = self.post_child(
                    parent_id,
                    new_entity_type_id,
                    child,
                    file_content=get_file_content_from_s3(child),
                    dry_run=dry_run,
                    old_to_new_ids_mapping=old_to_new_ids_mapping,
                )

                if recursive:
                    self.post_children(
                        created_child["id"],
                        child["children"],
                        entity_type_mapping,
                        dry_run=dry_run,
                        recursive=recursive,
                        old_to_new_ids_mapping=old_to_new_ids_mapping,
                    )
            except KeyError:
                print(f'Could not find entity type for {child["name"]}. Skipping entity and all its children.')

    def post_entity_tree(
        self,
        entity_tree: EntityDict,
        entity_type_mapping: dict,
        parent_id: int = None,
        dry_run: bool = False,
        old_to_new_ids_mapping: Optional[Dict] = None,
    ) -> None:
        """Iterates through the entity tree using a recursive function. Prompts the user for the top level entity.

        If dry_run is set to True, the recursive functions doesn't post to destination, but prints to screen
        """
        is_root = entity_tree["parent_entity_type"] is None
        if is_root:  # Parent entity will be the Root entity itself. Will only post the children entity types
            click.secho("Note: Cannot create Root entity. Will only create child entities.", fg="bright_yellow")
            nr_entities = entity_tree["size"] - 2
            parent_entity_type = entity_type_mapping[entity_tree["entity_type"]]
            children = entity_tree["children"]
        else:
            nr_entities = entity_tree["size"] - 1
            parent_entity_type = entity_type_mapping[entity_tree["parent_entity_type"]]
            children = [entity_tree]

        progressbar_label = f'Posting entities to {self.name} {"(dry run)" if dry_run else ""}'
        parent_id = parent_id or self._get_id_from_possible_entity_types(parent_entity_type)
        with click.progressbar(length=nr_entities, label=progressbar_label) as progressbar:
            self._progressbar = progressbar
            self.post_children(
                parent_id,
                children,
                entity_type_mapping,
                dry_run=dry_run,
                recursive=True,
                old_to_new_ids_mapping=old_to_new_ids_mapping,
            )

    def update_entity(
        self, entity_id: int, entity_properties: EntityDict, dry_run: bool = False, message: str = None
    ) -> dict:
        """Updates the entity with a new revision corresponding message"""
        if dry_run:
            print(entity_properties)
            print(f"Posting to entity {entity_id}: \n")
            return {"id": entity_id}
        data = {"message": message or "", "properties": entity_properties}
        response = self._put_request(f"/entities/{entity_id}/", data)
        return response

    def download_entities_of_type_to_local_folder(
        self, destination: str, entity_type_names: Tuple[str] = None, include_revisions: bool = True
    ) -> None:
        """Transfers entities of a specified type from current sub-domain to destination location

        as collection of json files
        """
        destination_dir = Path(f"{destination}")
        if not destination_dir.exists():
            destination_dir.mkdir()

        for entity_type in self.get_entity_types():
            if entity_type["class_name"] in entity_type_names:
                entity_type_dir = destination_dir / entity_type["class_name"]
                if not entity_type_dir.exists():
                    entity_type_dir.mkdir()

                print(
                    f'Getting all entities of type {entity_type["class_name"]} '
                    f"(can take a while if there are many entities)"
                )
                entities_of_specificed_type = self.get_all_entities_of_entity_type(entity_type["id"])
                with click.progressbar(
                    entities_of_specificed_type, label=f'Writing all entities of type {entity_type["class_name"]}'
                ) as progressbar:
                    for entity in progressbar:
                        if include_revisions:
                            for i, rev in enumerate(self.get_entity_revisions(entity["id"])):
                                with (entity_type_dir / f'{entity["id"]}_rev{i}.json').open(mode="w+") as entity_file:
                                    json.dump(rev, entity_file)
                        else:
                            with (entity_type_dir / f'{entity["id"]}.json').open(mode="w+") as entity_file:
                                json.dump(entity, entity_file)
                print("All entities succesfully saved")

    def download_database_to_local_folder(self, destination: str, filename: str):
        """Transfers all entities from current sub-domain to destination location as a single json file"""
        database_dict = {}  # Set up a dict which will be written to a file in the end
        all_entities = []  # Make a list in which all root entities including children will be saved
        for root_entity in self.get_root_entities():
            all_entities.append(self.get_entity_tree(root_entity["id"]))  # Append all root entities and children

        database_dict["entities"] = all_entities
        database_dict["entity_types"] = self.get_entity_types()

        # Save this as a json
        destination_dir = Path(f"{destination}")
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination_path = destination_dir / filename
        with open(destination_path, "w") as dest_file:
            json.dump(database_dict, dest_file)
        print(f"Stashed database in {destination_path}")

    def upload_database_from_local_folder(self, source_folder: str, filename: str):
        """Transfers all entities from current sub-domain to destination location as a single json file"""
        with open(Path(f"{source_folder}") / filename, "r") as db_file:
            database_dict = json.load(db_file)  # Get the database file
        entity_types = self.get_entity_types()
        destination_root_entities = self.get_root_entities()
        validate_root_entities_compatibility(database_dict["entities"], destination_root_entities)

        print("Successfully validated database compatibility. Removing children...")
        click.confirm(
            "[DANGER] This removes all current entities in this workspace. Do you want to continue?", abort=True
        )
        for root_entity in destination_root_entities:
            self.delete_children(root_entity["id"])

        # Make an entity type mapping from source entity type -> destination entity type
        entity_type_mapping = get_entity_type_mapping_from_entity_types(
            source_entity_types=database_dict["entity_types"], destination_entity_types=entity_types
        )

        print("Uploading database...")
        old_to_new_ids_mapping = {}
        for source_root_entity, destination_root_entity in zip(database_dict["entities"], destination_root_entities):
            # Let's first set the revisions
            self.update_entity(
                destination_root_entity["id"], source_root_entity["properties"], message="Apply database stash"
            )
            old_to_new_ids_mapping.update(
                {source_root_entity["id"]: destination_root_entity["id"]}
            )  # Update the entity map
            # Then let's upload the children
            self.post_entity_tree(
                source_root_entity,
                entity_type_mapping=entity_type_mapping,
                parent_id=destination_root_entity["id"],
                old_to_new_ids_mapping=old_to_new_ids_mapping,
            )

        print("Replacing entity IDs...")
        parametrization_dict = {}  # Make a dict to store entity_type and its field names that refer to entity types
        for entity_id in old_to_new_ids_mapping.values():  # For every entity that is uploaded to the database
            entity = self.get_entity(entity_id)  # Get the entity
            if entity["entity_type"] not in parametrization_dict:  # Parametrization is not yet analysed for this type
                parametrization = self.get_parametrization(entity_id)
                field_names_list_container = []  # Set up the ID fields list
                if parametrization:
                    add_field_names_referring_to_entities_to_container(
                        parametrization["content"]["parametrization"], field_names_list_container
                    )
                parametrization_dict.update({entity["entity_type"]: field_names_list_container})
            if field_names_list_container := parametrization_dict[entity["entity_type"]]:
                properties = entity["properties"]
                for field_names_list in field_names_list_container:
                    update_id_on_entity_fields(
                        field_names_list=field_names_list,
                        properties=properties,
                        old_to_new_ids_mapping=old_to_new_ids_mapping,
                    )
                self.update_entity(entity_id, properties)
        print("Successfully applied stashed database!")

    def add_user(self, user: ViktorUserDict):
        user_data = {
            "email": user["email"],
            "first_name": user["first_name"],
            "is_dev": bool(user.get("is_dev")),
            "is_env_admin": bool(user.get("is_env_admin")),
            "is_external": bool(user.get("is_external")),
            "job_title": user.get("job_title"),
            "last_name": user["last_name"],
            "send_activation_email": True,
        }
        response = self._post_request("/users/", data=user_data, exclude_workspace=True)
        if not response:
            print(f"Failed to add user: {response['first_name']} {response['last_name']}")
        else:
            print(f"Successfully added user: {response['name']}")

    def _get_id_from_possible_entity_types(self, parent_entity_type: int) -> int:
        """Obtains all possible entity id's of the entity type under which the entities can be posted

        If only one entity id can be found, this one is selected by default.
        """
        possible_parent_entities = self.get_all_entities_of_entity_type(parent_entity_type)
        default_id = possible_parent_entities[0]["id"]
        if len(possible_parent_entities) == 1:
            return default_id

        print("Destination parent entities: \n" + _repr_entities(possible_parent_entities) + "\n")
        destination = int(click.prompt("Under which parent id should the entities be copied", default=default_id))

        possible_parent_entity_ids = [entity["id"] for entity in possible_parent_entities]
        while destination not in possible_parent_entity_ids:
            destination = int(click.prompt("Entity id not possible, please try again", default=default_id))
        return destination

    def _update_file_download(self, entity: EntityDict):
        """If entity is file entity, converts the filename to a temporary download url"""
        if entity["properties"].get("filename", None):
            response = self._get_request(f"/entities/{entity['id']}/download/")
            entity["properties"]["filename"] = response["temporary_download_url"]

    def _clean_up_entity(self, entity: EntityDict):
        """Cleans up the entity, removing the entity id references and file references"""
        self._update_file_download(entity)

    # ============================== All DELETE requests ============================== #
    def delete_entity(self, entity_id: int) -> None:
        """Deletes an entity"""
        self._delete_request(f"/entities/{entity_id}/")

    def delete_children(self, entity_id: Union[int, Dict]):
        """Deletes all entities below some entity_id.

        entity_id may be given as a dict, to not again query the server if that has been done already"""
        parent = entity_id
        if not isinstance(entity_id, dict):
            parent = self.get_entity_tree(entity_id)
        child: dict
        for child in parent["children"]:
            self.delete_children(child)
            self.delete_entity(child["id"])
