"""Read, write, create, validate, and encode JSON packet definitions."""

import json
import re
import struct
from copy import deepcopy
from pathlib import Path
from typing import Any


FORMATS = {
    "int8_t": "b",
    "uint8_t": "B",
    "int16_t": "h",
    "uint16_t": "H",
    "int32_t": "i",
    "uint32_t": "I",
    "int64_t": "q",
    "uint64_t": "Q",
    "float": "f",
    "double": "d",
}
INTEGER_TYPES = set(FORMATS) - {"float", "double"}
SIZE_TERM_PATTERN = re.compile(r"size\(\s*([A-Za-z_]\w*)\s*\)")
CRC_TERM_PATTERN = re.compile(
    r"crc\(\s*(8|16)\s*,\s*(\d+)\s*,\s*"
    r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*,\s*"
    r"(\d+)\s*,\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*\)"
)


def parse_integer_value(value: int | str) -> int:
    if isinstance(value, int):
        return value
    text = value.strip().lower()
    unsigned_text = text.removeprefix("+").removeprefix("-")
    base = 0 if unsigned_text.startswith(("0x", "0b", "0o")) else 10
    return int(text, base)


def parse_size_expression(expression: str) -> list[str]:
    if not isinstance(expression, str):
        raise ValueError("Size expression must be a string")
    packet_names = []
    for term in expression.split("+"):
        match = SIZE_TERM_PATTERN.fullmatch(term.strip())
        if match is None:
            raise ValueError(f"Invalid size expression: {expression}")
        packet_names.append(match.group(1))
    return packet_names


def parse_crc_expression(expression: str) -> tuple[int, int, str, int, str]:
    if not isinstance(expression, str):
        raise ValueError("CRC expression must be a string")
    match = CRC_TERM_PATTERN.fullmatch(expression.strip())
    if match is None:
        raise ValueError(f"Invalid CRC expression: {expression}")
    start_position = int(match.group(2))
    end_position = int(match.group(4))
    if start_position < 1 or end_position < 1:
        raise ValueError("CRC packet positions must be at least 1")
    return (
        int(match.group(1)),
        start_position,
        match.group(3),
        end_position,
        match.group(5),
    )


def size(packet: dict[str, Any]) -> int:
    return _fields_size(packet["fields"])


def _fields_size(fields: list[dict[str, Any]]) -> int:
    total = 0
    for field in fields:
        kind = field.get("kind", "scalar")
        if kind == "struct":
            total += _fields_size(field["fields"])
        elif kind == "array":
            if "items" in field:
                total += _fields_size(field["items"])
            else:
                total += len(field["values"]) * struct.calcsize(
                    "<" + FORMATS[field["type"]]
                )
        else:
            total += struct.calcsize("<" + FORMATS[field["type"]])
    return total


def _dynamic_expressions(fields: list[dict[str, Any]]) -> set[str]:
    expressions = set()
    for field in fields:
        kind = field.get("kind", "scalar")
        if kind == "struct":
            expressions.update(_dynamic_expressions(field["fields"]))
        if kind == "array":
            if "items" in field:
                expressions.update(_dynamic_expressions(field["items"]))
            else:
                values = field.get("values", [])
                for value in values:
                    if isinstance(value, dict) and "dynamic" in value:
                        expressions.add(value["dynamic"])
        elif isinstance(field.get("value"), dict) and "dynamic" in field["value"]:
            expressions.add(field["value"]["dynamic"])
    return expressions


def packet_sequence_size_values(
    packets: list[dict[str, Any]],
    packet_catalog: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, int]]:
    if not packets:
        return []

    catalog = dict(packet_catalog or {})
    catalog.update((packet["name"], packet) for packet in packets)
    packet_sizes = {name: size(packet) for name, packet in catalog.items()}
    expressions = set().union(
        *(_dynamic_expressions(packet["fields"]) for packet in packets)
    )
    resolved_values: dict[str, int] = {}
    crc_expressions = []
    for expression in expressions:
        if expression.startswith("size("):
            referenced_packets = parse_size_expression(expression)
            missing_packets = [
                name for name in referenced_packets if name not in packet_sizes
            ]
            if missing_packets:
                names = ", ".join(missing_packets)
                raise ValueError(f"size() references unknown packet(s): {names}")
            resolved_values[expression] = sum(
                packet_sizes[name] for name in referenced_packets
            )
        else:
            crc_expressions.append(expression)

    flattened_fields = _flatten_sequence_fields(packets)
    active_crc_expressions: set[str] = set()

    def resolve_crc(expression: str) -> int:
        if expression in resolved_values:
            return resolved_values[expression]
        if expression in active_crc_expressions:
            raise ValueError(f"Cyclic CRC expression: {expression}")
        active_crc_expressions.add(expression)
        try:
            algorithm, start_position, start_path, end_position, end_path = (
                parse_crc_expression(expression)
            )
            start_index = _sequence_field_index(
                flattened_fields, start_position, start_path
            )
            end_index = _sequence_field_index(flattened_fields, end_position, end_path)
            if start_index > end_index:
                raise ValueError(
                    f"CRC start {start_position}:{start_path} is after "
                    f"end {end_position}:{end_path}"
                )
            crc_input = b"".join(
                _pack_crc_field(entry, resolved_values, resolve_crc)
                for entry in flattened_fields[start_index : end_index + 1]
            )
            result = _calculate_crc(algorithm, crc_input)
            resolved_values[expression] = result
            return result
        finally:
            active_crc_expressions.remove(expression)

    for expression in crc_expressions:
        resolve_crc(expression)
    return [dict(resolved_values) for _ in packets]


def _flatten_sequence_fields(
    packets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []

    def visit(fields: list[dict[str, Any]], packet_position: int, prefix: str = "") -> None:
        for field in fields:
            path = f"{prefix}.{field['name']}" if prefix else field["name"]
            kind = field.get("kind", "scalar")
            if kind == "struct":
                visit(field["fields"], packet_position, path)
            elif kind == "array" and "items" in field:
                visit(field["items"], packet_position, path)
            elif kind == "array":
                for index in range(len(field["values"])):
                    flattened.append({
                        "position": packet_position,
                        "path": f"{path}[{index}]",
                        "field": field,
                        "array_index": index,
                    })
            else:
                flattened.append({
                    "position": packet_position,
                    "path": path,
                    "field": field,
                })

    for position, packet in enumerate(packets, start=1):
        visit(packet["fields"], position)
    return flattened


def _sequence_field_index(
    flattened_fields: list[dict[str, Any]], packet_position: int, field_path: str
) -> int:
    for index, entry in enumerate(flattened_fields):
        if entry["position"] == packet_position and entry["path"] == field_path:
            return index
    raise ValueError(
        f"CRC endpoint field not found: {packet_position}:{field_path}"
    )


def _pack_crc_field(
    entry: dict[str, Any],
    resolved_values: dict[str, int],
    resolve_crc: Any,
) -> bytes:
    field = entry["field"]
    value = (
        field["values"][entry["array_index"]]
        if "array_index" in entry
        else field["value"]
    )
    if isinstance(value, dict) and "dynamic" in value:
        expression = value["dynamic"]
        if expression.startswith("size("):
            if expression not in resolved_values:
                raise ValueError(f"Unresolved size expression: {expression}")
            value = resolved_values[expression]
        else:
            value = resolve_crc(expression)
    if field["type"] in INTEGER_TYPES:
        value = parse_integer_value(value)
    try:
        return struct.pack("<" + FORMATS[field["type"]], value)
    except (KeyError, TypeError, ValueError, OverflowError, struct.error) as error:
        raise ValueError(f"Invalid CRC input field: {entry['path']}") from error


def _calculate_crc(algorithm: int, data: bytes) -> int:
    if algorithm == 8:
        crc = 0xFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                crc = (crc >> 1) ^ (0x8C if crc & 1 else 0)
        return crc

    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    return crc


def pack_packet_sequence(
    packets: list[dict[str, Any]],
    packet_catalog: dict[str, dict[str, Any]] | None = None,
) -> bytes:
    size_values = packet_sequence_size_values(packets, packet_catalog)
    return b"".join(
        pack_fields(packet["fields"], size_values[index])
        for index, packet in enumerate(packets)
    )


def validate_fields(fields: list[dict[str, Any]], allow_crc: bool = False) -> None:
    for field in fields:
        kind = field.get("kind", "scalar")
        if kind == "struct":
            if not isinstance(field.get("fields"), list):
                raise ValueError(f"Struct field {field.get('name')} must have a fields array")
            validate_fields(field["fields"], allow_crc)
        elif kind == "array":
            if "items" in field:
                items = field["items"]
                if not isinstance(items, list):
                    raise ValueError(f"Array field {field.get('name')} must have an items array")
                for item in items:
                    if item.get("type") != field.get("type"):
                        raise ValueError(f"Array item type mismatch in {field.get('name')}")
                validate_fields(items, allow_crc)
            elif field.get("type") in FORMATS and isinstance(field.get("values"), list):
                for value in field["values"]:
                    _validate_dynamic_value(value, field["type"], allow_crc)
            else:
                raise ValueError(f"Invalid array field: {field.get('name')}")
        elif kind == "scalar":
            if field.get("type") not in FORMATS or "value" not in field:
                raise ValueError(f"Invalid scalar field: {field.get('name')}")
            _validate_dynamic_value(field["value"], field["type"], allow_crc)
        else:
            raise ValueError(f"Unsupported field kind: {kind}")


def _validate_dynamic_value(
    value: Any, c_type: str, allow_crc: bool = False
) -> None:
    if not isinstance(value, dict) or "dynamic" not in value:
        return
    if set(value) != {"dynamic"}:
        raise ValueError("Dynamic values must contain only a dynamic expression")
    expression = value["dynamic"]
    if isinstance(expression, str) and expression.startswith("size("):
        if c_type not in INTEGER_TYPES:
            raise ValueError("Size expressions are supported only for integer fields")
        parse_size_expression(expression)
    elif isinstance(expression, str) and expression.startswith("crc("):
        if not allow_crc:
            raise ValueError("CRC expressions are supported only in packet sequences")
        if c_type not in INTEGER_TYPES:
            raise ValueError("CRC expressions are supported only for integer fields")
        parse_crc_expression(expression)
    else:
        raise ValueError(f"Unsupported dynamic expression: {expression}")


def pack_fields(
    fields: list[dict[str, Any]],
    dynamic_values: dict[str, int] | None = None,
) -> bytes:
    def resolve(value: Any) -> Any:
        if isinstance(value, dict) and "dynamic" in value:
            expression = value["dynamic"]
            if expression.startswith("size("):
                parse_size_expression(expression)
            else:
                parse_crc_expression(expression)
            if dynamic_values is None:
                return 0
            if expression not in dynamic_values:
                raise ValueError(f"Unresolved size expression: {expression}")
            return dynamic_values[expression]
        return value

    payload = bytearray()
    for field in fields:
        kind = field.get("kind", "scalar")
        if kind == "struct":
            payload.extend(pack_fields(field["fields"], dynamic_values))
        elif kind == "array":
            if "items" in field:
                payload.extend(pack_fields(field["items"], dynamic_values))
            else:
                values = field["values"]
                if field["type"] in INTEGER_TYPES:
                    values = [parse_integer_value(resolve(value)) for value in values]
                payload.extend(struct.pack("<" + FORMATS[field["type"]] * len(values), *values))
        else:
            value = resolve(field["value"])
            if field["type"] in INTEGER_TYPES:
                value = parse_integer_value(value)
            payload.extend(struct.pack("<" + FORMATS[field["type"]], value))
    return bytes(payload)


def unpack_fields(fields: list[dict[str, Any]], data: bytes, offset: int = 0) -> int:
    for field in fields:
        kind = field.get("kind", "scalar")
        if kind == "struct":
            offset = unpack_fields(field["fields"], data, offset)
        elif kind == "array":
            if "items" in field:
                offset = unpack_fields(field["items"], data, offset)
            else:
                c_type = field["type"]
                item_format = FORMATS[c_type]
                item_size = struct.calcsize("<" + item_format)
                item_count = len(field["values"])
                values = struct.unpack_from(
                    "<" + item_format * item_count,
                    data,
                    offset,
                )
                previous_values = field["values"]
                field["values"] = [
                    _preserve_hex_value(previous, value)
                    for previous, value in zip(previous_values, values)
                ]
                offset += item_size * item_count
        else:
            c_type = field["type"]
            item_format = FORMATS[c_type]
            value = struct.unpack_from("<" + item_format, data, offset)[0]
            field["value"] = _preserve_hex_value(field.get("value"), value)
            offset += struct.calcsize("<" + item_format)
    return offset


def _preserve_hex_value(previous: Any, value: int | float) -> int | float | str:
    if isinstance(previous, str) and previous.strip().lower().startswith("0x"):
        width = len(previous.strip()) - 2
        return f"0x{int(value):0{width}X}"
    return value


def read_packet_document(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("JSON 顶层必须是对象")
    if document.get("byte_order", "little") != "little" or document.get("packing", 1) != 1:
        raise ValueError("只支持 little 字节序和 packing=1 的数据包")
    return document


def document_packet_definitions(document: dict[str, Any]) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    if isinstance(document.get("packet"), dict):
        definitions.append(document["packet"])
    if "packet_sequence" not in document and "packet" not in document:
        if "name" in document and "fields" in document:
            definitions.append(document)
    for packet in definitions:
        validate_packet_definition(packet)
    return definitions


def validate_packet_definition(packet: Any) -> None:
    _validate_packet_definition(packet)


def _validate_packet_definition(packet: Any, allow_crc: bool = False) -> None:
    if not isinstance(packet, dict) or not isinstance(packet.get("name"), str):
        raise ValueError("数据包定义必须包含 name")
    if not isinstance(packet.get("fields"), list):
        raise ValueError(f"数据包 {packet['name']} 缺少 fields 数组")
    validate_fields(packet["fields"], allow_crc)
    pack_fields(packet["fields"])


def _resolve_packet_references(
    entries: list[Any],
    packet_catalog: dict[str, dict[str, Any]],
    allow_crc: bool = False,
) -> list[dict[str, Any]]:
    packets = []
    for entry in entries:
        if isinstance(entry, str):
            try:
                packet = packet_catalog[entry]
            except KeyError as error:
                raise ValueError(f"找不到被引用的数据包：{entry}") from error
            packets.append(deepcopy(packet))
        else:
            _validate_packet_definition(entry, allow_crc)
            packets.append(deepcopy(entry))
    return packets


def _collect_packet_catalog(
    directory: Path,
) -> tuple[dict[str, dict[str, Any]], list[Path]]:
    packet_catalog: dict[str, dict[str, Any]] = {}
    skipped_files: list[Path] = []
    json_files = sorted(
        (
            path for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() == ".json"
        ),
        key=lambda path: path.as_posix().casefold(),
    )
    for path in json_files:
        try:
            document = read_packet_document(path)
            for packet in document_packet_definitions(document):
                packet_catalog[packet["name"]] = packet
        except (OSError, ValueError, TypeError, KeyError, AttributeError, struct.error):
            skipped_files.append(path)
    return packet_catalog, skipped_files


def load_packet_file(
    path: Path,
    packet_catalog: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    document = read_packet_document(path)
    is_sequence = isinstance(document.get("packet_sequence"), list)
    if is_sequence:
        catalog = packet_catalog
        if catalog is None:
            catalog, _ = _collect_packet_catalog(Path.cwd())
            if path.parent != Path.cwd():
                local_catalog, _ = _collect_packet_catalog(path.parent)
                catalog.update(local_catalog)
        packets = _resolve_packet_references(
            document["packet_sequence"], catalog, allow_crc=True
        )
    elif isinstance(document.get("packet"), dict):
        packets = [deepcopy(document["packet"])]
    elif "name" in document and "fields" in document:
        packets = [document]
    else:
        raise ValueError("JSON 中没有数据包定义或 packet_sequence")

    for packet in packets:
        _validate_packet_definition(packet, allow_crc=is_sequence)
    if not packets:
        raise ValueError("文件中没有数据包")
    return packets, is_sequence


def load_packets_from_directory(
    directory: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Path]]:
    packet_catalog, skipped_files = _collect_packet_catalog(directory)
    compositions: list[dict[str, Any]] = []
    json_files = sorted(
        (
            path for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() == ".json"
        ),
        key=lambda path: path.as_posix().casefold(),
    )
    for path in json_files:
        try:
            document = read_packet_document(path)
            if not isinstance(document.get("packet_sequence"), list):
                continue
            packets = _resolve_packet_references(
                document["packet_sequence"], packet_catalog, allow_crc=True
            )
            for packet in packets:
                _validate_packet_definition(packet, allow_crc=True)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, struct.error):
            if path not in skipped_files:
                skipped_files.append(path)
            continue
        compositions.append({
            "name": path.stem,
            "display_name": path.stem,
            "description": f"组合数据包 · {len(packets)} 个子包",
            "path": path,
            "packets": deepcopy(packets),
        })
    names = [composition["display_name"] for composition in compositions]
    for composition in compositions:
        if names.count(composition["display_name"]) > 1:
            composition["display_name"] = (
                composition["path"].relative_to(directory).with_suffix("").as_posix()
            )
    return list(packet_catalog.values()), compositions, skipped_files


def save_packet_document(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
