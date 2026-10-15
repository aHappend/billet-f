"""Yosys hierarchy, storage, parallel arithmetic, generic synthesis and diagnostics."""

from collections import Counter
import json

from support import ROOT, relative, require, write_json


def state_bits(module):
    return sum(len(cell["connections"].get("Q", []))
               for cell in module.get("cells", {}).values()
               if "dff" in cell["type"].lower())


def live(bits):
    return any(isinstance(bit, int) for bit in bits)


def check_hierarchy(data, taps, lanes):
    modules = data["modules"]
    top = modules["billet_f2_top"]
    found = {}
    for instance, cell in top["cells"].items():
        if cell["type"] not in modules:
            continue
        child = modules[cell["type"]]
        name = child.get("attributes", {}).get("hdlname", cell["type"]).split()[0].lstrip("\\")
        expected = {
            "billet_f2_coeff_bank": "u_coeff_bank", "billet_f2_history": "u_history",
            "billet_f2_mac": "u_mac", "billet_f2_round_sat": "u_round_sat",
        }
        require(name in expected, f"unexpected direct child: {name}")
        require(instance.split(".")[-1] == expected[name], f"wrong instance: {instance}")
        meaningful = {
            "billet_f2_coeff_bank": ("cfg_write", "cfg_channel", "cfg_data", "commit",
                                     "active_banks", "staging_complete", "coeff_lanes"),
            "billet_f2_history": ("push", "push_channel", "push_data", "sample_lanes"),
            "billet_f2_mac": ("start", "sample_channel", "sample_bank", "sample_lanes",
                              "coeff_lanes", "busy", "final_valid", "final_sum", "final_channel"),
            "billet_f2_round_sat": ("accumulator", "result"),
        }
        require(all(live(cell["connections"][port]) for port in meaningful[name]),
                f"disconnected substantive child interface {instance}")
        found[name] = child
    require(len(found) == 4, "required four literal module boundaries are absent")
    coeff = state_bits(found["billet_f2_coeff_bank"])
    history = state_bits(found["billet_f2_history"])
    mac = state_bits(found["billet_f2_mac"])
    require(coeff >= 132 * taps + 4, "coefficient bank/mask state missing")
    require(history >= 64 * taps, "four independent histories missing")
    minimum_mac = 4 if taps == 1 else 32 + (taps - 1).bit_length() + 4
    require(mac >= minimum_mac, "substantive MAC state missing")
    require(state_bits(top) >= 19, "registered output state missing")
    multipliers = []
    mac_module = found["billet_f2_mac"]
    for cell in mac_module["cells"].values():
        if cell["type"] == "$mul":
            params = cell["parameters"]
            require(int(params["A_WIDTH"], 2) == 16 and int(params["B_WIDTH"], 2) == 16,
                    "MAC multiplier operands are not signed 16-bit")
            require(int(params["A_SIGNED"], 2) == int(params["B_SIGNED"], 2) == 1,
                    "unsigned MAC multiplier")
            require(live(cell["connections"]["A"]) and live(cell["connections"]["B"]) and
                    live(cell["connections"]["Y"]), "disconnected multiplier")
            matching_lanes = [
                lane for lane in range(lanes)
                if cell["connections"]["A"] == mac_module["ports"]["sample_lanes"]["bits"][16*lane:16*lane+16]
                and cell["connections"]["B"] == mac_module["ports"]["coeff_lanes"]["bits"][16*lane:16*lane+16]
            ]
            require(len(matching_lanes) == 1, "multiplier is not a distinct wired operand lane")
            multipliers.append({"A_WIDTH": 16, "B_WIDTH": 16,
                                "Y_WIDTH": int(params["Y_WIDTH"], 2), "signed": True,
                                "lane": matching_lanes[0]})
    useful_lanes = [m["lane"] for m in multipliers if m["lane"] < min(taps, lanes)]
    require(sorted(useful_lanes) == list(range(min(taps, lanes))),
            "missing usable parallel MAC paths")
    require(len({m["lane"] for m in multipliers}) == len(multipliers),
            "duplicate multiplier lane wiring")
    require(min(taps, lanes) <= len(multipliers) <= lanes, "invalid multiplier structure")
    for name, module in modules.items():
        for cell in module.get("cells", {}).values():
            require("latch" not in cell["type"].lower(), f"inferred latch in {name}")
    require(live(top["ports"]["m_data"]["bits"]) and live(top["ports"]["m_valid"]["bits"]),
            "constant-output top")
    return {"coefficient_state_bits": coeff, "history_state_bits": history,
            "mac_state_bits": mac, "top_state_bits": state_bits(top),
            "mac_parallel_multipliers": [m for m in multipliers if m["lane"] < min(taps, lanes)],
            "mac_multiplier_paths_before_flattening": multipliers,
            "tail_lane_paths_before_flattening": len(multipliers) - min(taps, lanes),
            "direct_module_instances": sorted(found)}


def synthesis_cell(evidence, directory, taps, lanes):
    directory.mkdir(parents=True)
    pre = directory / "hierarchy.json"
    final = directory / "netlist.json"
    script = directory / "synth.ys"
    rtl = [relative(p) for p in sorted(ROOT.glob("rtl/*.sv"))]
    script.write_text(
        "read_verilog -sv " + " ".join(rtl) + "\n"
        f"hierarchy -check -top billet_f2_top -chparam TAP_COUNT {taps} -chparam MAC_LANES {lanes}\n"
        "proc\nopt\nwreduce\nopt\ncheck -assert\n"
        "select -assert-none t:$dlatch t:$adlatch t:$_DLATCH_*\n"
        f"write_json {relative(pre)}\n"
        f"tee -o {relative(directory / 'hierarchy-stat.txt')} stat\n"
        "synth -top billet_f2_top -flatten -noabc\n"
        "check -assert\nselect -assert-none t:$dlatch t:$adlatch t:$_DLATCH_*\n"
        f"write_json {relative(final)}\n"
        f"write_verilog -noattr {relative(directory / 'netlist.v')}\n"
        f"tee -o {relative(directory / 'statistics.json')} stat -json\n")
    evidence.run(["yosys", "-Q", "-T", "-s", relative(script)], directory / "yosys")
    structure = check_hierarchy(json.loads(pre.read_text()), taps, lanes)
    module = json.loads(final.read_text())["modules"]["billet_f2_top"]
    types = Counter(cell["type"] for cell in module["cells"].values())
    require(not any("latch" in kind.lower() for kind in types), "final inferred latch")
    require(state_bits(module) >= 196 * taps + 4, "final storage optimized away")
    for output in ("s_ready", "cfg_ready", "commit_ready", "m_valid", "m_data", "m_channel"):
        require(live(module["ports"][output]["bits"]), f"constant public output: {output}")
    sizes = {}
    for p in sorted(ROOT.glob("rtl/*.sv")):
        sizes[relative(p)] = {"bytes": p.stat().st_size, "lines": len(p.read_text().splitlines())}
    result = {
        "status": "passed", "taps": taps, "lanes": lanes, "structure": structure,
        "generic_cells": sum(types.values()), "generic_cell_types": dict(types),
        "generic_sequential_bits": state_bits(module), "latches": 0,
        "source_sizes": sizes, "source_bytes": sum(s["bytes"] for s in sizes.values()),
        "source_lines": sum(s["lines"] for s in sizes.values()), "source_modules": len(sizes),
        "physical_area_or_frequency": "not measured; generic synthesis only",
        "receipt": relative(directory / "yosys/receipt.json"),
    }
    write_json(directory / "result.json", result)
    return result


def illegal_parameters(evidence, directory):
    directory.mkdir()
    results = []
    for taps, lanes in [(0, 1), (3, 1), (65, 1), (8, 0), (8, 3), (8, 8)]:
        cell = directory / f"t{taps}-l{lanes}"
        cell.mkdir()
        binary = cell / "diagnostic.vvp"
        argv = ["iverilog", "-g2012", "-Wall", "-s", "billet_f2_top",
                f"-Pbillet_f2_top.TAP_COUNT={taps}", f"-Pbillet_f2_top.MAC_LANES={lanes}",
                "-o", relative(binary)] + [relative(p) for p in sorted(ROOT.glob("rtl/*.sv"))]
        evidence.run(argv, cell / "compile")
        record = evidence.run(["vvp", relative(binary)], cell / "simulate", expected=None)
        text = (cell / "simulate/stdout.log").read_text() + (cell / "simulate/stderr.log").read_text()
        require(record["exit_code"] > 0 and "PARAMETER ERROR" in text,
                f"missing diagnostic/nonzero exit for {taps}/{lanes}")
        result = {"taps": taps, "lanes": lanes, "compile_exit": 0,
                  "simulation_exit": record["exit_code"], "diagnostic": "PARAMETER ERROR",
                  "receipt": relative(cell / "simulate/receipt.json"), "status": "passed"}
        write_json(cell / "result.json", result)
        results.append(result)
    return results
