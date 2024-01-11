"""This module contains some related functions for a Viktor Sub-domain"""
import copy
from typing import Dict
from typing import List
from typing import Union


def validate_root_entities_compatibility(source_root_entities, destination_root_entities):
    """Validate that the manifest file still generated the same root entities"""
    error_message = (
        "It appears the manifest has changed since your last stash. Please restore the root entities in "
        "the manifest file as they were stashed with."
    )
    assert len(destination_root_entities) == len(source_root_entities), error_message
    for source_root_entity, destination_root_entity in zip(source_root_entities, destination_root_entities):
        assert source_root_entity["entity_type_name"] == destination_root_entity["entity_type_name"], error_message


def add_field_names_referring_to_entities_to_container(
    parametrization_fields_list: List, field_names_list_container: List[List]
):
    """Get all the field names that refer to some entity.

    :param parametrization_fields_list: Use ViktorSubDomain().get_parametrization(entity_id)["parametrization"] as input
    :param field_names_list_container: The result is added to the field_names_list_container. Looks like:
        [['page_name', 'tab_name', 'field_name'], ['page_name', 'tab_name', 'array_name', ['field_name']]]
    """
    for field_dict in parametrization_fields_list:
        if "entity" in field_dict["type"]:
            if field_dict.get("entity_type_name"):
                field_names_list_container.append(field_dict["entity_type_name"])
            if field_dict.get("name"):
                field_names_list_container.append(field_dict["name"])
        if "content" in field_dict.keys():
            add_field_names_referring_to_entities_to_container(field_dict["content"], field_names_list_container)
        if "arrayItems" in field_dict.keys():
            new_container = []
            add_field_names_referring_to_entities_to_container(field_dict["arrayItems"], new_container)
            if new_container:
                field_names_list_container.append(field_dict["name"].split(".") + new_container)


def update_id_on_entity_fields(
    field_names_list: List[Union[str, List]], properties: Union[Dict, List], old_to_new_ids_mapping: Dict[int, int]
):
    """Update the ID on entity ID related fields

    Parameters
    ----------
    field_names_list : List of field names where a field resides that refers to some entity.
        Generated by get_field_names_referring_to_entities
    properties : Properties dictionary or list of properties of an entity. Does not have to be top level necessarily
    old_to_new_ids_mapping : Mapping dictionary of old_entity_id -> new_entity_id
    """
    field_names_list = copy.deepcopy(field_names_list)  # Copy to not edit anything in-place, list may be reused
    if isinstance(field_names_list, list):
        key_or_list = field_names_list.pop(0)
        if isinstance(key_or_list, str):
            key = key_or_list
            if len(field_names_list) == 0:  # If last item, set the new values
                if isinstance(properties, dict):
                    if isinstance(properties[key], int):
                        properties[key] = old_to_new_ids_mapping[properties[key]]
                    elif isinstance(properties[key], list):  # Might be a multiple select field
                        for index, value in enumerate(properties[key]):
                            properties[key][index] = old_to_new_ids_mapping[value]
            else:  # Not the last item, go deeper
                if isinstance(properties, dict):
                    update_id_on_entity_fields(field_names_list, properties[key], old_to_new_ids_mapping)
        elif isinstance(key_or_list, list):  # Nested structure! Let's go deeper for each of those
            for row in properties:
                update_id_on_entity_fields(key_or_list, row, old_to_new_ids_mapping)
