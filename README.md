# jobcarbon

Carbon footprint of a Slurm job, and the waste worth fixing, from nothing but
its job identifier.

`jobcarbon` reads the job's record from Slurm (`sacct`), computes its carbon
footprint in gCO2e (IPCC 2021, GWP100) and flags two kinds of waste:

- **memory reserved but never used** (peak use under half of the reservation);
- **memory concentrated in one NUMA domain** while the job uses more cores than
  one domain has. This is checked live, by running `numastat` inside the job's
  own allocation while it runs.

For each flag it prints the change to make and how to check that it worked.
It is the tool of Section IV.D of the paper; the method it applies is:

1. **Recognise** — many threads on a multi-domain node, speed that stops growing
   past one domain's cores, and `numastat -p` showing most of the memory in one
   domain. (Slurm's CPU efficiency can still look close to 100 %: a core
   waiting for memory counts as busy.)
2. **Change** — launch under `numactl --interleave=all` (no code change), or
   initialise the data in parallel with the threads that use it.
3. **Check** — identical output (checksum or diff), memory spread over the
   domains, runtime and energy per run measured again the same way.

## Requirements

Python 3.8 or newer, standard library only. On a cluster: `sacct`, and for the
placement check `srun` and `numastat` (package `numactl`). Nothing to install.

## Usage

```bash
# On the cluster: footprint and flags of a finished job (whole-job estimate)
python3 jobcarbon.py 16068563 -M aion

# Measured per-run footprint, from a powercap recording written by measure.sh
python3 jobcarbon.py 16229728 -M aion --rapl results/16229728/rapl.tsv

# While a job is running: check where its memory sits
python3 jobcarbon.py <jobid> -M aion --probe-numa XSBench

# Without a cluster: from a saved sacct record, or from values directly
python3 jobcarbon.py --sacct-file examples/<jobid>.sacct
python3 jobcarbon.py --runtime 259.67 --energy-j 93135

# Machine-readable output
python3 jobcarbon.py --runtime 259.67 --energy-j 93135 --json
```

To save a record for offline use, run on the cluster:

```bash
sacct -j <jobid> -P -o JobID,JobName,Partition,NodeList,AllocNodes,AllocCPUS,ElapsedRaw,TotalCPU,ReqMem,MaxRSS,State > <jobid>.sacct
```

## What it prints

Reproducing the baseline figure of the paper (one run of XSBench as
distributed, Section III.E: 18.9 gCO2e), with no cluster needed:

```
$ python3 jobcarbon.py --runtime 259.67 --energy-j 93135
factors: aion (regular node), PUE 1.3, grid 337 gCO2e/kWh, embodied 960.7 kg/node over 8 y at 82% use
run 'RUN' (median of 1): 259.67 s, CPU 93135 J measured
  energy  CPU 25.87 + memory 6.88 + rest 7.60 = 40.34 Wh IT, 52.45 Wh with PUE
  carbon  17.67 g operational + 1.21 g embodied = 18.89 gCO2e
  i.e. 10.5 h of a 5 W LED, 65 m by petrol car
```

With `--rapl`, runs are grouped by their marker label (`BASE1..n`, `OPT1..n`,
`RUN1..n`); each group gives the median run, and if both `BASE` and `OPT` are
present the saving per run is printed.

## How the footprint is computed

```
IT energy   = CPU energy + (memory W + rest-of-node W) x duration
operational = IT energy x PUE x grid factor
embodied    = node manufacturing x duration / (lifetime x utilisation)
```

CPU energy comes, in order of preference, from a powercap recording
(`--rapl`), a value you measured (`--energy-j`), or the Green Algorithms model
(cores x TDP per core x CPU usage). A job that holds part of a node is charged
its share of cores. Default factors, for one regular Aion node:

| Factor | Value | Source |
|---|---|---|
| Memory power | 0.3725 W/GB x 256 GB | Lannelongue et al., Green Algorithms, 2021 |
| Rest of node | 105.3 W | Green500 node power minus CPU TDP and memory (paper III.C) |
| PUE | 1.3 (declared, range 1.10–1.67) | none published for Aion; see paper III.C |
| Grid | 337 gCO2e/kWh | ecoinvent v3.11 cut-off, electricity low voltage, LU, IPCC 2021 GWP100 |
| Node manufacturing | 960.7 kgCO2e | Dell PowerEdge R6525 LCA, adapted (paper Table A2) |
| Lifetime, utilisation | 8 years, 81.56 % | assumption; ULHPC workload analysis |

For another machine, write a JSON file with the keys of `AION` in
`jobcarbon.py` that differ, and pass it with `--config`.

## Limits

- The memory and rest-of-node power are modelled, not measured.
- The whole-job estimate without `--rapl` uses TDP and overestimates
  memory-bound jobs (by about 50 % on CPU energy for XSBench on Aion).
- The NUMA check needs the job to be running; afterwards, placement cannot be
  read back from Slurm.
- The flags say where waste is likely; step 3 of the method is what proves a
  change helped.

## Licence

MIT, see `LICENSE`.
