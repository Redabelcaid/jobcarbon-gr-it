# jobcarbon

`jobcarbon` estimates the carbon footprint of a Slurm job from its job
identifier and reports two common sources of waste: reserved memory that the
job never uses, and memory concentrated in a single NUMA domain of a
multi-domain node. For each finding it states the corrective change and how to
verify it.

The footprint follows a life cycle assessment of one job: the operational
emissions of the allocated node (processors, memory, rest of the node, data
centre overhead) and the share of the node's manufacturing allocated to the
time the job holds it. Results are expressed in gCO2e under IPCC 2021 GWP100.

## Requirements

- Python 3.8 or later; standard library only.
- On a Slurm cluster: `sacct`; for the NUMA check, also `srun` and `numastat`
  (package `numactl`).
- Without a cluster, the tool runs on a saved `sacct` record or on values
  supplied on the command line.

No installation is needed. In the commands below, `jobcarbon.py` stands for the
path to the script, e.g. `~/jobcarbon/jobcarbon.py`.

## Usage

```
python3 jobcarbon.py [JOBID] [options]
```

| Option | Meaning |
|---|---|
| `JOBID` | Slurm job identifier |
| `-M, --cluster NAME` | Slurm cluster to query, for multi-cluster sites |
| `--rapl FILE` | Powercap recording of the job; enables measured per-run energy (format below) |
| `--runtime S`, `--energy-j J` | Duration (s) and measured processor energy (J) of one run, without Slurm |
| `--probe-numa PROCESS` | While the job runs, read the NUMA placement of `PROCESS` inside its allocation |
| `--sacct-file FILE` | Read a saved `sacct` record instead of querying Slurm |
| `--config FILE` | JSON file overriding the default hardware and site factors |
| `--json` | Machine-readable output |

## Examples

Footprint and findings for a finished job:

```bash
python3 jobcarbon.py <jobid>
```

Check the memory placement of a running job (replace `myprog` by the name of
the executable):

```bash
python3 jobcarbon.py <jobid> --probe-numa myprog
```

Measured per-run footprint, from a powercap recording:

```bash
python3 jobcarbon.py <jobid> --rapl rapl.tsv
```

Offline, from a saved record:

```bash
sacct -j <jobid> -P -o JobID,JobName,Partition,NodeList,AllocNodes,AllocCPUS,ElapsedRaw,TotalCPU,ReqMem,MaxRSS,State > job.sacct
python3 jobcarbon.py --sacct-file job.sacct
```

## Output

The baseline run of XSBench on one Aion node, as reported in the accompanying
paper, is reproduced with:

```
$ python3 jobcarbon.py --runtime 259.67 --energy-j 93135
factors: aion (regular node), PUE 1.3, grid 337 gCO2e/kWh, embodied 960.7 kg/node over 8 y at 82% use
run 'RUN' (median of 1): 259.67 s, CPU 93135 J measured
  energy  CPU 25.87 + memory 6.88 + rest 7.60 = 40.34 Wh IT, 52.45 Wh with PUE
  carbon  17.67 g operational + 1.21 g embodied = 18.89 gCO2e
  i.e. 10.5 h of a 5 W LED, 65 m by petrol car
```

For a job given by its identifier, the output also lists the allocation, the
CPU efficiency, the reserved and peak memory, and any findings, each with the
recommended change.

## Method

**Energy.** Processor energy is taken, in order of preference, from a powercap
recording, from a value measured by the user, or from the Green Algorithms
model (cores × TDP per core × CPU usage). Memory and the rest of the node are
always modelled:

```
IT energy   = processor energy + (P_memory + P_rest) × duration
operational = IT energy × PUE × grid factor
embodied    = node manufacturing × duration / (lifetime × utilisation)
```

A job holding part of a node is charged its share of the cores.

**NUMA finding.** A job that uses more cores than one NUMA domain provides can
lose most of its memory bandwidth when its data is initialised by a single
thread: the Linux first-touch policy then places every page in one domain. The
check reads `numastat -p` inside the job's allocation; if one domain holds more
than 60 % of the process memory, the tool recommends launching the program
under `numactl --interleave=all`, or initialising the data in parallel with the
threads that use it, and verifying the change by an identical output, a
balanced `numastat`, and a new measurement of runtime and energy.

**Memory finding.** Peak resident memory below half of the reservation is
reported, with a suggested `--mem` value. On an exclusive node this frees
resources only if exclusivity is also dropped.

## Default factors

The defaults describe a regular node of the Aion cluster (University of
Luxembourg). For another system, provide the keys that differ in a JSON file
passed with `--config`; the full set of keys is the `AION` dictionary in
`jobcarbon.py`.

| Factor | Value | Source |
|---|---|---|
| Processors | 2 × AMD EPYC 7H12, 280 W TDP, 128 cores | ULHPC documentation |
| Memory power | 0.3725 W/GB × 256 GB | Lannelongue et al., *Green Algorithms*, Adv. Sci. 2021 |
| Rest of node | 105.3 W | Green500 node power minus processor TDP and memory |
| PUE | 1.3 (plausible range 1.10–1.67) | declared; not published for Aion |
| Grid factor | 337 gCO2e/kWh | ecoinvent v3.11 cut-off, electricity, low voltage, LU, IPCC 2021 GWP100 |
| Node manufacturing | 960.7 kgCO2e | Dell PowerEdge R6525 LCA, adapted to the Aion node |
| Lifetime, utilisation | 8 years, 81.56 % | assumption; ULHPC workload analysis |

## Powercap recording format

`--rapl` expects a whitespace-separated text file: a header line
`# kind label unix_time <zone>:<name>:max=<wrap> ...`, then one line per
reading, `SAMPLE - <time> <µJ per zone>` for periodic samples and
`MARK <LABEL>_START|<LABEL>_END <time> <µJ per zone>` around each run. Runs are
grouped by label without its trailing number (e.g. `BASE1..5`, `OPT1..5`); the
median of each group is reported, and the saving per run when both `BASE` and
`OPT` are present. A job script producing this format is part of the
accompanying study.

## Limitations

- Memory and rest-of-node power are modelled, not measured.
- Without a powercap recording, the TDP-based estimate overestimates
  memory-bound jobs (by about 50 % on processor energy for XSBench on Aion).
- NUMA placement can only be observed while the job runs.
- Findings indicate likely waste; only a new measurement shows that a change
  reduced the footprint.

