"""AnythingMCP REST connector tool definitions, not a second execution path."""

from codebound.models import ScriptWrite, ExecutionRequest


def tools():
    write = ScriptWrite.model_json_schema()
    execute = ExecutionRequest.model_json_schema()
    identifier = {"type": "string", "format": "uuid"}
    specs = [
        ("list_datasets", "GET", "/datasets", {}, [], None),
        ("list_scripts", "GET", "/scripts", {}, [], None),
        ("create_script", "POST", "/scripts", write["properties"], write["required"], write),
        ("get_script", "GET", "/scripts/{script_id}", {"script_id": identifier}, ["script_id"], None),
        ("update_script", "PUT", "/scripts/{script_id}", {"script_id": identifier, **write["properties"]},
         ["script_id", *write["required"]], write),
        ("delete_script", "DELETE", "/scripts/{script_id}", {"script_id": identifier}, ["script_id"], None),
        ("execute_script", "POST", "/scripts/{script_id}/execute", {"script_id": identifier, **execute["properties"]},
         ["script_id", *execute["required"]], execute),
    ]
    definitions = []
    for name, method, path, properties, required, body in specs:
        schema = {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
        if body and "$defs" in body:
            schema["$defs"] = body["$defs"]
        mapping = {"method": method, "path": path, "encodePathParams": True}
        if body:
            mapping["bodyMapping"] = {key: "$" + key for key in body["properties"]}
        definitions.append({"name": "codebound_" + name, "description": "Authorized Codebound " + name.replace("_", " "),
                            "parameters": schema, "endpointMapping": mapping})
    return definitions


def ai_tools():
    from codebound.llm.models import GenerateRequest, ModifyRequest
    definitions = []
    for name, path, model, has_id in [
        ("generate_script", "/ai/scripts/generate", GenerateRequest, False),
        ("modify_script", "/ai/scripts/{script_id}/modify", ModifyRequest, True),
        ("run_task", "/ai/tasks/run", GenerateRequest, False),
    ]:
        schema = model.model_json_schema()
        mapping = {"method": "POST", "path": path, "encodePathParams": True,
                   "bodyMapping": {key: "$" + key for key in schema["properties"]}}
        if has_id:
            schema["properties"]["script_id"] = {"type": "string", "format": "uuid"}
            schema.setdefault("required", []).append("script_id")
        definitions.append({"name": "codebound_" + name, "description": "Authorized local AI " + name.replace("_", " "),
                            "parameters": schema, "endpointMapping": mapping})
    return definitions
