"""Byte-exact writer/codec/restore identity, without native V4 promotion authority."""
import ast
from types import MappingProxyType

from . import archive_components as components


SCHEMA = "rc6.archive-writer-codec-restorer-binding.v1"
SOURCE_MEMBERS = (
    "rc6_shadow_runtime/archive_components.py", "rc6_shadow_runtime/exact_page_storage.py",
    "rc6_shadow_runtime/exact_binary_storage.py", "rc6_shadow_runtime/retention.py",
    "rc6_shadow_runtime/persistence.py", "rc6_shadow_runtime/archive_namespace.py",
    "rc6_shadow_runtime/archive_format_contract.py", "rc6_shadow_runtime/worker.py",
)
ENTRYPOINTS = MappingProxyType({
    "native_writer": ("rc6_shadow_runtime/archive_components.py", "ComponentArchive.build"),
    "native_v4_writer": ("rc6_shadow_runtime/archive_components.py", "ComponentArchive.build_v4"),
    "native_v4_write_guard": ("rc6_shadow_runtime/archive_components.py", "require_native_v4_write_capability"),
    "original_source_custody": ("rc6_shadow_runtime/archive_components.py", "ComponentArchive._build_original_source"),
    "historical_v3_writer": ("rc6_shadow_runtime/archive_components.py", "ComponentArchive.build_v3"),
    "native_runtime_dispatch": ("rc6_shadow_runtime/worker.py", "ShadowRuntime.from_environment"),
    "effective_original_v3_writer": ("rc6_shadow_runtime/archive_components.py", "ComponentArchive.publish_original_v3"),
    "proposed_v4_planner": ("rc6_shadow_runtime/archive_components.py", "ComponentArchive._comparison_slice_plan"),
    "proposed_effective_v4_writer": ("rc6_shadow_runtime/archive_components.py", "ComponentArchive.publish_original_v4"),
    "shared_writer": ("rc6_shadow_runtime/archive_components.py", "ComponentArchive._publish_original_plan"),
    "shared_publication": ("rc6_shadow_runtime/archive_components.py", "ComponentArchive._publish_new_original_plan"),
    "sequential_object_write": ("rc6_shadow_runtime/archive_components.py", "ComponentArchive._publish"),
    "sequential_control_write": ("rc6_shadow_runtime/retention.py", "EvidenceRetention._durable_control"),
    "typed_recipe_reader": ("rc6_shadow_runtime/archive_components.py", "ComponentArchive._indices"),
    "exact_restore": ("rc6_shadow_runtime/archive_components.py", "ComponentArchive.restore"),
    "transitive_whole_pack_graph": ("rc6_shadow_runtime/archive_components.py", "ComponentArchive.dependency_graph"),
    "native_ack": ("rc6_shadow_runtime/retention.py", "EvidenceRetention._archive_generation"),
    "native_gc": ("rc6_shadow_runtime/retention.py", "EvidenceRetention._maintain_archive"),
    "native_restore": ("rc6_shadow_runtime/retention.py", "EvidenceRetention.restore_generation"),
    "page_encoder": ("rc6_shadow_runtime/exact_page_storage.py", "encode_page_pack"),
    "page_decoder": ("rc6_shadow_runtime/exact_page_storage.py", "decode_page_pack"),
    "binary_encoder": ("rc6_shadow_runtime/exact_binary_storage.py", "encode_binary_pack"),
    "binary_decoder": ("rc6_shadow_runtime/exact_binary_storage.py", "decode_binary_pack"),
})


def _require(value, signature):
    if not value:
        raise ValueError(signature)


def _function_bytes(raw, qualified):
    tree = ast.parse(raw.decode())
    nodes, selected = tree.body, None
    for name in qualified.split("."):
        selected = next((node for node in nodes if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                         and node.name == name), None)
        _require(selected is not None, "ARCHIVE_FORMAT_EXACT_ENTRYPOINT_MISSING")
        nodes = selected.body
    lines = raw.splitlines(keepends=True)
    return b"".join(lines[selected.lineno - 1:selected.end_lineno])


def _closed_file_write_inventory(raw, qualified, *, control):
    """Verify the exact two sequential writers used by the ext4 envelope.

    This checks Source premises, without treating hashes as semantic proof.
    A new call, seek, loop, random write, allocator operation or file creator
    requires a new bound; it cannot inherit this inventory automatically.
    """
    body = _function_bytes(raw, qualified)
    tree = ast.parse(body.decode() if not body.startswith(b"    ") else __import__("textwrap").dedent(body.decode()))
    def name(node):
        if isinstance(node, ast.Name): return node.id
        if isinstance(node, ast.Attribute): return name(node.value) + "." + node.attr
        return "UNKNOWN"
    allowed = {"ValueError", "os.open", "os.fdopen", "os.fsync", "uuid.uuid4", "stream.write",
        "stream.flush", "stream.fileno", "temporary.unlink", "path.is_symlink", "path.exists"}
    allowed |= ({"path.stat", "json.dumps", "len", "temporary.replace", "self._sync_directory"}
                if control else {"read", "max", "len", "temporary.rename", "self.owner._fault", "self.owner._sync_directory",
                                 "self.owner._archive_inventory"})
    # Encode on the bounded json result is the one nested attribute expression.
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While, ast.AsyncFor, ast.Await, ast.Yield, ast.YieldFrom)):
            raise ValueError("ARCHIVE_FORMAT_SEQUENTIAL_WRITER_NEW_CONTROL_FLOW")
        if isinstance(node, ast.Call):
            current = name(node.func)
            if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call)
                    and name(node.func.value.func) == "json.dumps" and node.func.attr == "encode"):
                current = "json.dumps.encode"
            _require(current in allowed | {"json.dumps.encode"}, "ARCHIVE_FORMAT_SEQUENTIAL_WRITER_NEW_OPERATION")
            if current == "os.open":
                flags = {name(term) for term in ast.walk(node.args[1]) if isinstance(term, ast.Attribute)}
                _require(flags == {"os.O_WRONLY", "os.O_CREAT", "os.O_EXCL", "os.O_NOFOLLOW"}
                    and len(node.args) == 3 and isinstance(node.args[2], ast.Constant) and node.args[2].value == 0o600,
                    "ARCHIVE_FORMAT_SEQUENTIAL_WRITER_FLAGS_OR_MODE_REBOUND")
            if current == "stream.write":
                _require(len(node.args) == 1 and isinstance(node.args[0], ast.Name)
                    and node.args[0].id == ("data" if control else "raw"),
                    "ARCHIVE_FORMAT_SEQUENTIAL_WRITER_BYTES_NOT_KNOWN")
            calls.append(current)
    _require(calls.count("os.open") == calls.count("os.fdopen") == calls.count("stream.write") == calls.count("os.fsync") == 1,
             "ARCHIVE_FORMAT_ONE_SEQUENTIAL_NEW_FILE_WRITER_REQUIRED")
    return {"qualified_name": qualified, "source_sha256": components.sha(body), "calls": sorted(calls),
            "one_active_new_regular_inode": True, "sequential_known_byte_write": True}


def writer_codec_restorer_binding(source_files):
    """Derive from actual captured Source bytes; an inventory is not a semantic proof."""
    _require(type(source_files) is dict and set(SOURCE_MEMBERS) <= set(source_files)
             and all(type(source_files[name]) is bytes and 0 < len(source_files[name]) <= 4 * 1024**2
                     for name in SOURCE_MEMBERS), "ARCHIVE_FORMAT_COMPLETE_SOURCE_BYTES_REQUIRED")
    files = {name: components.sha(source_files[name]) for name in SOURCE_MEMBERS}
    points = {role: {"path": path, "qualified_name": name,
                    "source_sha256": components.sha(_function_bytes(source_files[path], name))}
              for role, (path, name) in ENTRYPOINTS.items()}
    # Report the effective V3 dispatch and the blocked V4 proposal separately.
    # Actual execution, full replay and future artifact bytes are separate joins.
    native = ast.parse(__import__("textwrap").dedent(_function_bytes(source_files[SOURCE_MEMBERS[0]], "ComponentArchive.build").decode())).body[0]
    _require(len(native.body) == 2 and isinstance(native.body[1], ast.Return)
        and isinstance(native.body[1].value, ast.Call)
        and ast.unparse(native.body[1].value.func) == "self.build_v3"
        and [ast.unparse(value) for value in native.body[1].value.args] == ["source", "manifest", "manifest_sha"],
        "ARCHIVE_FORMAT_EFFECTIVE_NATIVE_V3_DISPATCH_REBOUND")
    v4 = ast.parse(__import__("textwrap").dedent(_function_bytes(source_files[SOURCE_MEMBERS[0]], "ComponentArchive.build_v4").decode())).body[0]
    _require(len(v4.body) == 3 and isinstance(v4.body[1], ast.Expr)
        and ast.unparse(v4.body[1].value) == "require_native_v4_write_capability()"
        and isinstance(v4.body[2], ast.Return) and isinstance(v4.body[2].value, ast.Call)
        and ast.unparse(v4.body[2].value.func) == "self._build_original_source"
        and [(keyword.arg, ast.unparse(keyword.value)) for keyword in v4.body[2].value.keywords]
            == [("writer", "self.publish_original_v4")], "ARCHIVE_FORMAT_CANDIDATE_V4_PLANNER_REBOUND")
    public_v4 = ast.parse(__import__("textwrap").dedent(_function_bytes(source_files[SOURCE_MEMBERS[0]], "ComponentArchive.publish_original_v4").decode())).body[0]
    _require(len(public_v4.body) == 3 and isinstance(public_v4.body[1], ast.Expr)
        and ast.unparse(public_v4.body[1].value) == "require_native_v4_write_capability()",
        "ARCHIVE_FORMAT_DIRECT_V4_PUBLICATION_GUARD_REQUIRED")
    guard = ast.parse(_function_bytes(source_files[SOURCE_MEMBERS[0]], "require_native_v4_write_capability").decode()).body[0]
    _require(isinstance(guard.body[-1], ast.Raise)
        and ast.unparse(guard.body[-1].exc) == "ValueError('RETENTION_V4_WRITE_APPROVAL_CERTIFICATION_ARTIFACT_REQUIRED')",
        "ARCHIVE_FORMAT_LIVE_V4_CAPABILITY_ISSUER_REQUIRED")
    factory = ast.parse(__import__("textwrap").dedent(_function_bytes(source_files["rc6_shadow_runtime/worker.py"], "ShadowRuntime.from_environment").decode()))
    choices = [node for node in ast.walk(factory) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault"
               and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "archive_format"]
    _require(len(choices) == 1 and len(choices[0].args) == 2 and isinstance(choices[0].args[1], ast.Constant)
        and choices[0].args[1].value == "COMPONENT_V3", "ARCHIVE_FORMAT_EFFECTIVE_RUNTIME_FACTORY_REBOUND")
    result = {"schema": SCHEMA, "source_files_sha256": files, "entrypoints": points,
        "read_recipe_schemas": [components.RECIPE_SCHEMA, components.SLICE_RECIPE_SCHEMA],
        "ack_schema": components.ACK_SCHEMA, "native_write_recipe_schema": components.RECIPE_SCHEMA,
        "proposed_recipe_schema": components.SLICE_RECIPE_SCHEMA,
        "effective_product_writer_choice": "NATIVE_COMPONENT_V3_PENDING_V4_ACTIVATION",
        "proposed_product_writer_choice": "CANDIDATE_V2_NATIVE_COMPONENT_V4",
        "candidate_native_write_recipe_schema": components.SLICE_RECIPE_SCHEMA,
        "native_runtime_archive_format": "COMPONENT_V3",
        "effective_native_writer": "ComponentArchive.build",
        "effective_native_planner": "ComponentArchive.publish_original_v3",
        "candidate_contract_accepted": False,
        "proposed_v4_writer_choice": "OWNER_ACCEPTANCE_G5_AND_EXACT_G7_G8_ARTIFACT_REQUIRED",
        "artifact_writer_v4_promotion_approved": False, "native_v4_write_enabled": False}
    result["closed_file_write_inventory"] = [
        _closed_file_write_inventory(source_files["rc6_shadow_runtime/archive_components.py"], "ComponentArchive._publish", control=False),
        _closed_file_write_inventory(source_files["rc6_shadow_runtime/retention.py"], "EvidenceRetention._durable_control", control=True)]
    result["binding_sha256"] = components.sha(components.canonical(result))
    return result


def verify_immutable_artifact_binding(expected, *, source_files, artifact_files):
    """Recompute both sides from full authenticated source/artifact member captures."""
    source = writer_codec_restorer_binding(source_files)
    artifact = writer_codec_restorer_binding(artifact_files)
    _require(expected == source == artifact and all(source_files[name] == artifact_files[name]
             for name in SOURCE_MEMBERS), "ARCHIVE_FORMAT_WRITER_CODEC_RESTORER_ARTIFACT_REBOUND")
    return {"status": "EXACT_BYTES_MATCHED_NOT_PROMOTION", "binding_sha256": source["binding_sha256"],
            "native_v4_write_enabled": False, "runtime_validated": False, "real_orders_sent": 0}
