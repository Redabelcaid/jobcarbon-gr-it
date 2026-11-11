#!/usr/bin/env python3
"""Carbon footprint of a Slurm job, and the waste worth fixing. See README.md."""
import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

AION = {
    "name": "aion (regular node)",
    "cores_per_node": 128,
    "numa_domains": 8,
    "tdp_w_per_core": 280 / 64,
    "mem_gb_per_node": 256,
    "mem_w_per_gb": 0.3725,
    "rest_of_node_w": 105.3,
    "pue": 1.3,
    "grid_g_per_kwh": 337,
    "embodied_kg_per_node": 960.7,
    "lifetime_years": 8,
    "utilisation": 0.8156,
}
LED_W = 5
CAR_G_PER_KM = 291

def parse_duration(s):
    """Slurm [DD-]HH:MM:SS(.mmm) or MM:SS(.mmm) -> seconds."""
    s = s.strip()
    if not s:
        return 0.0
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [float(p) for p in s.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, sec = parts
    return days * 86400 + h * 3600 + m * 60 + sec

def parse_mem_mb(s):
    """Slurm memory field (e.g. 224000M, 5801816K, 218.75G, 4000Mc) -> MB."""
    m = re.match(r"([\d.]+)\s*([KMGT]?)", s.strip())
    if not m:
        return 0.0
    v, u = float(m.group(1)), m.group(2)
    return v * {"K": 1 / 1024, "": 1 / 1024 / 1024, "M": 1, "G": 1024, "T": 1024 ** 2}.get(u, 1)

SACCT_FIELDS = "JobID,JobName,Partition,NodeList,AllocNodes,AllocCPUS,ElapsedRaw,TotalCPU,ReqMem,MaxRSS,State"

def read_sacct(jobid, sacct_file=None, cluster=None):
    if sacct_file:
        text = Path(sacct_file).read_text()
    else:
        cmd = ["sacct", "-j", str(jobid), "-P", "-o", SACCT_FIELDS]
        if cluster:
            cmd += ["-M", cluster]
        try:
            text = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError) as e:
            sys.exit(f"jobcarbon: cannot run sacct ({e}). Without a cluster, use --sacct-file "
                     f"or --runtime/--energy-j (see README).")
    lines = [l for l in text.splitlines() if l.strip()]
    header = lines[0].split("|")
    rows = [dict(zip(header, l.split("|"))) for l in lines[1:]]
    if not rows:
        sys.exit(f"jobcarbon: no record for job {jobid}")
    job = next((r for r in rows if "." not in r["JobID"]), rows[0])
    steps = [r for r in rows if "." in r["JobID"]]
    rss = max([parse_mem_mb(r.get("MaxRSS", "")) for r in steps] or [0.0])
    tcpu = sum(parse_duration(r.get("TotalCPU", "")) for r in steps) or parse_duration(job.get("TotalCPU", ""))
    return {
        "jobid": job["JobID"], "name": job.get("JobName", ""), "partition": job.get("Partition", ""),
        "nodes": job.get("NodeList", ""), "n_nodes": int(job.get("AllocNodes") or 1),
        "cpus": int(job.get("AllocCPUS") or 0), "elapsed_s": float(job.get("ElapsedRaw") or 0),
        "totalcpu_s": tcpu, "reqmem_mb": parse_mem_mb(job.get("ReqMem", "")),
        "maxrss_mb": rss, "state": job.get("State", ""),
    }

def rapl_runs(path):
    """Per-interval CPU package energy from a measure.sh recording (rapl.tsv)."""
    zones, readings = [], []
    for line in Path(path).read_text().splitlines():
        if line.startswith("#"):
            for col in line.split()[4:]:
                body, mx = col.rsplit(":max=", 1)
                zid, name = body.rsplit(":", 1)
                zones.append({"name": name, "max": int(mx)})
            continue
        p = line.split()
        if len(p) < 3 + len(zones):
            continue
        readings.append((float(p[2]), p[0], p[1], [int(v) for v in p[3:3 + len(zones)]]))
    readings.sort(key=lambda r: r[0])
    total, prev, marks = [0.0] * len(zones), readings[0][3], {}
    for t, kind, label, vals in readings:
        for k, z in enumerate(zones):
            d = vals[k] - prev[k]
            total[k] += (d + z["max"] if d < 0 else d) / 1e6
        prev = vals
        if kind == "MARK":
            marks[label] = (t, list(total))
    pkg = [k for k, z in enumerate(zones) if z["name"].startswith("package")]
    out = {}
    for lab in {l[:-6] for l in marks if l.endswith("_START")}:
        if lab + "_END" in marks:
            (t0, e0), (t1, e1) = marks[lab + "_START"], marks[lab + "_END"]
            out[lab] = (t1 - t0, sum(e1[k] - e0[k] for k in pkg))
    return out

def footprint(runtime_s, cpu_j, f, node_share=1.0, n_nodes=1):
    """Operational + embodied gCO2e for one node-share held for runtime_s."""
    mem_w = f["mem_gb_per_node"] * f["mem_w_per_gb"] * node_share * n_nodes
    rest_w = f["rest_of_node_w"] * node_share * n_nodes
    it_wh = (cpu_j + (mem_w + rest_w) * runtime_s) / 3600
    site_wh = it_wh * f["pue"]
    op_g = site_wh / 1000 * f["grid_g_per_kwh"]
    life_s = f["lifetime_years"] * 365 * 24 * 3600 * f["utilisation"]
    emb_g = f["embodied_kg_per_node"] * 1000 * runtime_s / life_s * node_share * n_nodes
    return {"cpu_wh": cpu_j / 3600, "mem_wh": mem_w * runtime_s / 3600, "rest_wh": rest_w * runtime_s / 3600,
            "it_wh": it_wh, "site_wh": site_wh, "op_g": op_g, "emb_g": emb_g, "total_g": op_g + emb_g}

def probe_numa(jobid, process, cluster=None):
    """Run numastat inside a running job's allocation; return (shares per node, raw text)."""
    cmd = ["srun", f"--jobid={jobid}", "--overlap", "-N1", "-n1", "numastat", "-p", process]
    if cluster:
        cmd.insert(1, f"-M{cluster}")
    try:
        txt = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"probe failed: {e}"
    for line in txt.splitlines():
        if line.strip().startswith("Total"):
            vals = [float(v) for v in line.split()[1:]]
            node_vals = vals[:-1] if len(vals) > 1 else vals
            tot = sum(node_vals) or 1.0
            return [v / tot for v in node_vals], txt
    return None, txt or "probe returned nothing (is the job running? is the process name right?)"

def main():
    ap = argparse.ArgumentParser(description="Carbon footprint of a Slurm job, and the waste worth fixing.")
    ap.add_argument("jobid", nargs="?", help="Slurm job identifier")
    ap.add_argument("-M", "--cluster", help="Slurm cluster name (e.g. aion), passed to sacct/srun")
    ap.add_argument("--sacct-file", help="saved output of: sacct -j ID -P -o " + SACCT_FIELDS)
    ap.add_argument("--rapl", help="powercap recording (rapl.tsv) written by measure.sh")
    ap.add_argument("--runtime", type=float, help="duration of one run, s (with --energy-j)")
    ap.add_argument("--energy-j", type=float, help="measured CPU package energy of one run, J")
    ap.add_argument("--probe-numa", metavar="PROCESS", help="check memory placement of PROCESS in the running job")
    ap.add_argument("--config", help="JSON file overriding the default (Aion) factors")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args()

    f = dict(AION)
    if a.config:
        f.update(json.loads(Path(a.config).read_text()))

    job = None
    if a.jobid or a.sacct_file:
        job = read_sacct(a.jobid, a.sacct_file, a.cluster)
    elif a.runtime is None:
        ap.error("give a job id, --sacct-file, or --runtime with --energy-j")

    report = {"factors": f, "job": job, "runs": {}, "flags": []}
    say = []

    def p(s=""):
        say.append(s)

    p(f"factors: {f['name']}, PUE {f['pue']}, grid {f['grid_g_per_kwh']} gCO2e/kWh, "
      f"embodied {f['embodied_kg_per_node']} kg/node over {f['lifetime_years']} y at {f['utilisation']:.0%} use")

    share, n_nodes = 1.0, 1
    if job:
        n_nodes = job["n_nodes"]
        share = min(1.0, job["cpus"] / (f["cores_per_node"] * n_nodes)) if job["cpus"] else 1.0
        eff = job["totalcpu_s"] / (job["elapsed_s"] * job["cpus"]) if job["elapsed_s"] and job["cpus"] else 0
        p(f"job {job['jobid']} ({job['name']}), partition {job['partition']}, {job['nodes']}, state {job['state']}")
        p(f"  held {job['elapsed_s']:.0f} s on {n_nodes} node(s), {job['cpus']} cores "
          f"({share:.0%} of the node), CPU efficiency {eff:.0%}")
        p(f"  memory reserved {job['reqmem_mb'] / 1024:.2f} GiB, peak used {job['maxrss_mb'] / 1024:.2f} GiB")

    if a.rapl:
        runs = rapl_runs(a.rapl)
        groups = {}
        for lab, (t, e) in runs.items():
            if lab == "IDLE":
                continue
            groups.setdefault(re.sub(r"\d+$", "", lab), []).append((t, e))
        for g, vals in sorted(groups.items()):
            t = statistics.median(v[0] for v in vals)
            e = statistics.median(v[1] for v in vals)
            r = footprint(t, e, f)
            report["runs"][g] = {"n": len(vals), "runtime_s": t, "cpu_j": e, **r}
        if "IDLE" in runs:
            t, e = runs["IDLE"]
            report["idle_cpu_w"] = e / t
    elif a.runtime is not None:
        e = a.energy_j if a.energy_j is not None else None
        if e is None:
            e = f["cores_per_node"] * f["tdp_w_per_core"] * a.runtime
        report["runs"]["RUN"] = {"n": 1, "runtime_s": a.runtime, "cpu_j": e, **footprint(a.runtime, e, f)}

    if job and job["elapsed_s"]:
        usage = min(1.0, job["totalcpu_s"] / (job["elapsed_s"] * job["cpus"])) if job["cpus"] else 1.0
        cpu_j = job["cpus"] * f["tdp_w_per_core"] * usage * job["elapsed_s"]
        report["whole_job_model"] = footprint(job["elapsed_s"], cpu_j, f, share, n_nodes)

    for g, r in report["runs"].items():
        p(f"run '{g}' (median of {r['n']}): {r['runtime_s']:.2f} s, CPU {r['cpu_j']:.0f} J measured"
          if (a.rapl or a.energy_j is not None) else
          f"run '{g}': {r['runtime_s']:.2f} s, CPU {r['cpu_j']:.0f} J (TDP model)")
        p(f"  energy  CPU {r['cpu_wh']:.2f} + memory {r['mem_wh']:.2f} + rest {r['rest_wh']:.2f} "
          f"= {r['it_wh']:.2f} Wh IT, {r['site_wh']:.2f} Wh with PUE")
        p(f"  carbon  {r['op_g']:.2f} g operational + {r['emb_g']:.2f} g embodied = {r['total_g']:.2f} gCO2e")
        p(f"  i.e. {r['site_wh'] / LED_W:.1f} h of a 5 W LED, {r['total_g'] / CAR_G_PER_KM * 1000:.0f} m by petrol car")
    if "BASE" in report["runs"] and "OPT" in report["runs"]:
        b, o = report["runs"]["BASE"]["total_g"], report["runs"]["OPT"]["total_g"]
        p(f"saving per run OPT vs BASE: {b - o:.2f} gCO2e ({(b - o) / b:.0%})")
    if "whole_job_model" in report:
        w = report["whole_job_model"]
        p(f"whole job, Green Algorithms model (TDP x usage): {w['total_g']:.2f} gCO2e "
          f"({w['site_wh']:.1f} Wh with PUE)")

    if job:
        if job["reqmem_mb"] and job["maxrss_mb"] and job["maxrss_mb"] < 0.5 * job["reqmem_mb"]:
            unused = 1 - job["maxrss_mb"] / job["reqmem_mb"]
            report["flags"].append("memory")
            p(f"FLAG memory: {unused:.0%} of the reserved memory was never used.")
            p(f"  change: request about {job['maxrss_mb'] * 1.2 / 1024:.0f} GiB (--mem) instead of "
              f"{job['reqmem_mb'] / 1024:.0f} GiB, so the rest of the node can serve other jobs;")
            p("  on an exclusive node this frees nothing unless the job also drops --exclusive.")
        dom_cores = f["cores_per_node"] // f["numa_domains"]
        if job["cpus"] > dom_cores:
            if a.probe_numa:
                shares, raw = probe_numa(job["jobid"], a.probe_numa, a.cluster)
                if shares:
                    top = max(shares)
                    p(f"numastat: largest NUMA domain holds {top:.1%} of the memory of {a.probe_numa}")
                    if top > 0.6 and len(shares) > 1:
                        report["flags"].append("numa")
                        p("FLAG numa: memory concentrated in one domain while the job uses "
                          f"{job['cpus']} cores ({job['cpus'] // dom_cores} domains' worth).")
                        p("  change: launch with  numactl --interleave=all <your command>  (no code change),")
                        p("          or initialise the data in parallel with the threads that use it.")
                        p("  check:  same output (checksum/diff), numastat spread over domains,")
                        p("          runtime and energy per run measured again the same way.")
                        p("  reference: XSBench on Aion, see paper Sect. IV.C.")
                else:
                    p(f"numastat probe: {raw.strip()[:200]}")
            else:
                p(f"note: the job uses {job['cpus']} cores, more than one NUMA domain ({dom_cores}). "
                  f"While it runs, add --probe-numa <process name> to check its memory placement.")

    if a.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print("\n".join(say))

if __name__ == "__main__":
    main()
