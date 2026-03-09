#!/usr/bin/env python3
"""
Convert ComfyUI UI-format workflow JSON to API-format.

Handles:
- GetNode/SetNode global variable resolution (replaced with direct connections)
- Group Nodes (subgraphs) expansion from embedded definitions
- Bypassed nodes (mode=4) are skipped
- Frontend-only nodes (MarkdownNote, Fast Groups Bypasser, etc.) are skipped

Usage:
    python tools/convert_workflow.py workflows/input.json [workflows/output_api.json]
    Requires a running ComfyUI instance (default http://localhost:8188).
"""

import json
import sys
import urllib.request


def fetch_object_info(base_url: str = "http://localhost:8188") -> dict:
    resp = urllib.request.urlopen(f"{base_url}/object_info", timeout=10)
    return json.loads(resp.read())


_SKIP_TYPES = {
    "Reroute", "Note", "PrimitiveNode",
    "GetNode", "SetNode",
    "Fast Groups Bypasser (rgthree)",
    "MarkdownNote",
}

_VALUE_PRODUCER_TYPES = {
    "mxSlider", "SamplerSelector", "SchedulerSelector",
    "FloatConstant", "INTConstant", "PrimitiveFloat",
}


def _is_widget_input(input_spec) -> bool:
    if not isinstance(input_spec, (list, tuple)) or len(input_spec) == 0:
        return False
    typ = input_spec[0]
    return isinstance(typ, list) or typ in ("INT", "FLOAT", "STRING", "BOOLEAN")


def _get_widget_names(node_type: str, obj_info: dict) -> list[str]:
    if node_type not in obj_info:
        return []
    info = obj_info[node_type]
    names = []
    for section in ("required", "optional"):
        for name, spec in info.get("input", {}).get(section, {}).items():
            if _is_widget_input(spec):
                names.append(name)
    return names


def _resolve_getset_variables(nodes, links):
    link_map = {}
    for link in links:
        link_map[link[0]] = {"src_node": link[1], "src_slot": link[2]}

    node_map = {n["id"]: n for n in nodes}

    def _trace_through_bypass(node_id, slot):
        visited = set()
        while node_id in node_map and node_id not in visited:
            nd = node_map[node_id]
            if nd.get("mode", 0) != 4:
                return (node_id, slot)
            visited.add(node_id)
            for inp in nd.get("inputs", []):
                inp_link = inp.get("link")
                if inp_link and inp_link in link_map:
                    lk = link_map[inp_link]
                    node_id = lk["src_node"]
                    slot = lk["src_slot"]
                    break
            else:
                break
        return (node_id, slot)

    set_vars = {}
    for n in nodes:
        if n.get("type") == "SetNode":
            var_name = (n.get("widgets_values") or [""])[0]
            if isinstance(var_name, dict):
                var_name = ""
            for inp in n.get("inputs", []):
                link_id = inp.get("link")
                if link_id and link_id in link_map:
                    lk = link_map[link_id]
                    src_id, src_slot = _trace_through_bypass(lk["src_node"], lk["src_slot"])
                    set_vars[var_name] = (src_id, src_slot)

    get_resolution = {}
    for n in nodes:
        if n.get("type") == "GetNode":
            var_name = (n.get("widgets_values") or [""])[0]
            if isinstance(var_name, dict):
                var_name = ""
            if var_name in set_vars:
                get_resolution[n["id"]] = set_vars[var_name]

    return get_resolution


def _resolve_value_producers(nodes, links):
    values = {}
    for n in nodes:
        if n.get("type", "") not in _VALUE_PRODUCER_TYPES:
            continue
        wv = n.get("widgets_values")
        if wv is None:
            continue
        if isinstance(wv, dict):
            for v in wv.values():
                if not isinstance(v, dict):
                    values[n["id"]] = v
                    break
        elif isinstance(wv, (list, tuple)) and len(wv) > 0:
            values[n["id"]] = wv[0]
    return values


def _expand_group_nodes(ui_workflow: dict) -> dict:
    """Expand Group Nodes (subgraphs) into their inner standard nodes."""
    subgraphs = ui_workflow.get("definitions", {}).get("subgraphs", [])
    if not subgraphs:
        return ui_workflow

    sg_map = {sg["id"]: sg for sg in subgraphs}
    nodes = list(ui_workflow.get("nodes", []))
    links = list(ui_workflow.get("links", []))

    max_node_id = max((n["id"] for n in nodes), default=0)
    max_link_id = max((l[0] for l in links), default=0)
    link_lookup = {l[0]: l for l in links}

    nodes_to_add = []
    links_to_add = []
    nodes_to_remove = set()
    links_to_remove = set()
    # Track: group_node_id -> {input_slot: (remapped_inner_node_id, inner_slot)}
    group_input_map = {}
    # Track: group_node_id -> {output_slot: (remapped_inner_node_id, inner_slot)}
    group_output_map = {}
    # Track: group_node_id -> inner_id_map
    group_id_maps = {}

    for node in nodes:
        sg_id = node.get("type", "")
        if sg_id not in sg_map or node.get("mode", 0) == 4:
            continue

        sg = sg_map[sg_id]
        instance_id = node["id"]
        id_offset = max_node_id + 1
        max_node_id += 10000

        inner_nodes = sg.get("nodes", [])
        inner_links = sg.get("links", [])

        # Build ID remap
        inner_id_map = {inn["id"]: inn["id"] + id_offset for inn in inner_nodes}
        group_id_maps[instance_id] = inner_id_map

        # Create remapped inner nodes
        added_for_this = {}
        for inn in inner_nodes:
            new_node = dict(inn)
            new_node["id"] = inner_id_map[inn["id"]]
            new_node["inputs"] = [dict(inp, link=None) for inp in new_node.get("inputs", [])]
            new_node["outputs"] = [dict(out, links=[]) for out in new_node.get("outputs", [])]
            nodes_to_add.append(new_node)
            added_for_this[new_node["id"]] = new_node

        # Record input/output slot -> inner node mapping (for cross-subgraph fixing)
        input_map = {}
        output_map = {}
        for il in inner_links:
            if isinstance(il, dict):
                o_id, o_slot, t_id, t_slot = il["origin_id"], il["origin_slot"], il["target_id"], il["target_slot"]
                il_type = il.get("type", "")
            else:
                o_id, o_slot, t_id, t_slot = il[1], il[2], il[3], il[4]
                il_type = il[5] if len(il) > 5 else ""

            max_link_id += 1
            nlid = max_link_id

            if o_id == -10:
                new_target = inner_id_map.get(t_id, t_id)
                input_map[o_slot] = (new_target, t_slot)
                # Try to resolve from external link
                ext_inp = node.get("inputs", [])
                if o_slot < len(ext_inp):
                    ext_lid = ext_inp[o_slot].get("link")
                    if ext_lid and ext_lid in link_lookup:
                        el = link_lookup[ext_lid]
                        new_link = [nlid, el[1], el[2], new_target, t_slot, il_type]
                        links_to_add.append(new_link)
                        nd = added_for_this.get(new_target)
                        if nd and t_slot < len(nd.get("inputs", [])):
                            nd["inputs"][t_slot]["link"] = nlid
                        links_to_remove.add(ext_lid)
            elif t_id == -20:
                new_origin = inner_id_map.get(o_id, o_id)
                output_map[t_slot] = (new_origin, o_slot)
                ext_out = node.get("outputs", [])
                if t_slot < len(ext_out):
                    for ext_lid in (ext_out[t_slot].get("links") or []):
                        if ext_lid in link_lookup:
                            el = link_lookup[ext_lid]
                            max_link_id += 1
                            new_link = [max_link_id, new_origin, o_slot, el[3], el[4], il_type]
                            links_to_add.append(new_link)
                            links_to_remove.add(ext_lid)
            else:
                new_origin = inner_id_map.get(o_id, o_id)
                new_target = inner_id_map.get(t_id, t_id)
                new_link = [nlid, new_origin, o_slot, new_target, t_slot, il_type]
                links_to_add.append(new_link)
                nd = added_for_this.get(new_target)
                if nd and t_slot < len(nd.get("inputs", [])):
                    nd["inputs"][t_slot]["link"] = nlid

        group_input_map[instance_id] = input_map
        group_output_map[instance_id] = output_map
        nodes_to_remove.add(instance_id)

    # Fix cross-subgraph links: when link source or target is a removed group node,
    # redirect to the correct inner node using group_output_map / group_input_map
    for link in links_to_add:
        # Fix target side: link points TO a removed group node
        dst_node_id = link[3]
        dst_slot = link[4]
        if dst_node_id in nodes_to_remove and dst_node_id in group_input_map:
            redir = group_input_map[dst_node_id]
            if dst_slot in redir:
                new_target, new_slot = redir[dst_slot]
                link[3] = new_target
                link[4] = new_slot
                # Update the target node's input link ref
                for na in nodes_to_add:
                    if na["id"] == new_target:
                        if new_slot < len(na.get("inputs", [])):
                            na["inputs"][new_slot]["link"] = link[0]
                        break

        # Fix source side: link comes FROM a removed group node
        src_node_id = link[1]
        src_slot = link[2]
        if src_node_id in nodes_to_remove and src_node_id in group_output_map:
            redir = group_output_map[src_node_id]
            if src_slot in redir:
                new_origin, new_slot = redir[src_slot]
                link[1] = new_origin
                link[2] = new_slot

    new_nodes = [n for n in nodes if n["id"] not in nodes_to_remove] + nodes_to_add
    new_links = [l for l in links if l[0] not in links_to_remove] + links_to_add

    result = dict(ui_workflow)
    result["nodes"] = new_nodes
    result["links"] = new_links
    return result


def convert(ui_workflow: dict, obj_info: dict) -> dict:
    """Convert a UI-format workflow dict to API-format prompt dict."""

    # First, expand Group Nodes into their inner nodes
    ui_workflow = _expand_group_nodes(ui_workflow)

    nodes = ui_workflow.get("nodes", [])
    links = ui_workflow.get("links", [])

    # Build link map
    link_map = {}
    for link in links:
        link_map[link[0]] = {
            "src_node": link[1],
            "src_slot": link[2],
            "type": link[5] if len(link) > 5 else "",
        }

    node_map = {n["id"]: n for n in nodes}

    get_resolution = _resolve_getset_variables(nodes, links)
    value_producers = _resolve_value_producers(nodes, links)

    api_workflow = {}

    for node in nodes:
        node_id = node["id"]
        node_type = node.get("type", "")

        if node_type in _SKIP_TYPES or node_type in _VALUE_PRODUCER_TYPES:
            continue
        if node.get("mode", 0) == 4:
            continue

        inputs = {}
        title = node.get("title", "")

        # 1. Map linked inputs
        for input_slot in node.get("inputs", []):
            input_name = input_slot["name"]
            link_id = input_slot.get("link")
            if link_id is None or link_id not in link_map:
                continue

            lk = link_map[link_id]
            src_node_id = lk["src_node"]
            src_slot = lk["src_slot"]

            src_node = node_map.get(src_node_id, {})
            if src_node.get("mode", 0) == 4:
                continue
            src_type = src_node.get("type", "")

            # Resolve GetNode
            if src_node_id in get_resolution:
                real_src_id, real_src_slot = get_resolution[src_node_id]
                visited = set()
                while real_src_id in get_resolution and real_src_id not in visited:
                    visited.add(real_src_id)
                    real_src_id, real_src_slot = get_resolution[real_src_id]
                if real_src_id in value_producers:
                    inputs[input_name] = value_producers[real_src_id]
                else:
                    inputs[input_name] = [str(real_src_id), real_src_slot]
                continue

            # Resolve value producers
            if src_node_id in value_producers:
                inputs[input_name] = value_producers[src_node_id]
                continue

            if src_type in _SKIP_TYPES:
                continue

            inputs[input_name] = [str(src_node_id), src_slot]

        # 2. Map widgets_values
        widgets_values = node.get("widgets_values")
        if widgets_values is not None:
            widget_names = _get_widget_names(node_type, obj_info)

            if isinstance(widgets_values, dict):
                for wname, wval in widgets_values.items():
                    if isinstance(wval, dict):
                        continue
                    if wname not in inputs:
                        inputs[wname] = wval
            elif isinstance(widgets_values, list):
                wi = vi = 0
                while vi < len(widgets_values) and wi < len(widget_names):
                    wval = widgets_values[vi]
                    if isinstance(wval, dict):
                        vi += 1
                        continue
                    wname = widget_names[wi]
                    if wname not in inputs:
                        inputs[wname] = wval
                    wi += 1
                    vi += 1

        api_node = {"class_type": node_type, "inputs": inputs}
        if title:
            api_node["_meta"] = {"title": title}
        api_workflow[str(node_id)] = api_node

    return api_workflow


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <input.json> [output_api.json] [--comfyui-url URL]")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
    comfyui_url = "http://localhost:8188"
    for i, arg in enumerate(sys.argv):
        if arg == "--comfyui-url" and i + 1 < len(sys.argv):
            comfyui_url = sys.argv[i + 1]

    with open(input_path) as f:
        ui_workflow = json.load(f)

    if "nodes" not in ui_workflow:
        print("Already in API format, no conversion needed.", file=sys.stderr)
        result = json.dumps(ui_workflow, indent=2)
        if output_path:
            with open(output_path, "w") as f:
                f.write(result)
        else:
            print(result)
        return

    print(f"Fetching object_info from {comfyui_url}...", file=sys.stderr)
    obj_info = fetch_object_info(comfyui_url)

    node_types = {n.get("type", "") for n in ui_workflow["nodes"]}
    handled = _SKIP_TYPES | _VALUE_PRODUCER_TYPES
    missing = [t for t in node_types if t and t not in obj_info and t not in handled]
    if missing:
        print(f"WARNING: {len(missing)} node types not found in ComfyUI:", file=sys.stderr)
        for m in sorted(missing):
            bypassed = all(n.get("mode", 0) == 4 for n in ui_workflow["nodes"] if n.get("type") == m)
            print(f"  - {m}{' (bypassed, OK)' if bypassed else ''}", file=sys.stderr)

    api_workflow = convert(ui_workflow, obj_info)

    total_ui = len(ui_workflow["nodes"])
    total_api = len(api_workflow)
    skipped = sum(1 for n in ui_workflow["nodes"] if n.get("type", "") in handled)
    bypassed = sum(1 for n in ui_workflow["nodes"] if n.get("mode", 0) == 4)
    print(f"Converted: {total_ui} UI nodes -> {total_api} API nodes "
          f"({skipped} virtual, {bypassed} bypassed)", file=sys.stderr)

    result = json.dumps(api_workflow, indent=2, ensure_ascii=False)
    if output_path:
        with open(output_path, "w") as f:
            f.write(result)
        print(f"Saved to {output_path}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
