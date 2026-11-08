# Sim Atlas: what the literature says about building simulations, and what armlock can take

A knowledge graph of 71,637 arXiv papers (abstracts, 2009 to Nov 2026) on robot simulation, embodied safety and hazard physics, plus a hand-checked web layer on simulator makers and startups. Code and data are in `research/simgraph/`; the interactive page is the artifact "Sim Atlas" (private link given in chat).

## What is in the graph (and what is not)

| Layer | Size | Source |
|---|---|---|
| Papers (title, abstract, authors, categories) | 71,637 | arXiv API: 12 targeted queries (39,452) plus all of `cs.RO` up to arXiv's paging limit |
| Similarity links | 716,370 | each paper's 10 nearest neighbours by wording (TF-IDF + SVD, cosine) |
| Topic clusters | 120 | k-means on the same embedding |
| Entities and mentions | 101 entities, 117,317 mentions | regex lexicon: simulators, techniques, physics, safety, domains (`lexicon.py`) |
| Bridge papers | 18,541 | neighbours in 3+ clusters |
| Authors | 126,111 (358,105 links) | arXiv metadata, not disambiguated |
| Citations and affiliations | 4,256 papers, 80,629 citation links | OpenAlex, free tier, years 2012-2024 only |
| Industry layer | 28 nodes, source URL each | web search results (several are vendor or news pages) |

**Not reached: 100,000 sources.** `cs.RO` for 2025 and 2026 stops at arXiv's 10,000-result paging limit (HTTP 429); monthly slicing would add the rest. Citation links cover only 6% of papers because the free OpenAlex budget is about 100 requests a day; a free API key raises that tenfold. Citation links exist mostly for papers from 2012-2024, and about 45% of arXiv DOIs resolve in OpenAlex. Entity mentions are regex matches on abstracts, so a mention does not prove a paper uses the tool.

## Findings that matter for the training ground

1. **Hazard physics is almost absent from robot-simulation papers.** Of 71.6k papers, the lexicon finds `electrical` hazards in 14, `chemical` in 40, `harmful instructions` in 29, `force/pressure limits` in 45, `embodied safety benchmark` in 74 and `battery abuse` in 91. Searching for thermal-runaway, shock and chemical-mixing simulation returns plasma physics and generic friction papers instead (needs N3 to N5 in `query.py needs`). So armlock's five scenarios are not covered by an existing simulator you can import; the physics has to come from the hazard literature (see `docs/physics-changes.md`) and be modelled by us.
2. **The established recipe stack is clear.** Strongest pairings against chance: LIBERO x vision-language-action (521 papers vs 23 expected), SimplerEnv x VLA (73 vs 2), Gaussian splatting x photorealistic rendering (144 vs 6), Isaac x GPU-parallel simulation. Newton, MuJoCo Warp, Isaac Lab and MuJoCo Playground are converging on one GPU physics stack (NVIDIA Warp), per the industry layer.
3. **Gaps worth owning.** Rarely together, versus chance: human-robot collaboration x sim-to-real (28 vs 86), control barrier functions x benchmark (39 vs 153), collision avoidance x VLA (21 vs 75). Nobody pairs safety filters with benchmarks, which is what armlock does.
4. **Gate validation has no standard.** The closest papers are sim-to-real certification and safety transfer (`2603.20672` certified simulation-gap functions, `2509.18648` SPiDR zero-shot safety in sim-to-real, `2403.07091` Isaac sim-to-real gap case study) and benchmarks of agent safety (`2504.14650`, `2412.14470` Agent-SafetyBench, `2603.25747` BeSafe-Bench). None validates a gate against a text-only simulator. This supports the audit's rank 1: report circularity plainly and add independent ground truth.
5. **LLM-generated environments are an active way to scale the ground** (Eurekaverse `2411.01775` and the GenSim, RoboGen and Holodeck family in the industry layer). The web sources did not say how they assign or check physical properties, so do not assume they solve fidelity.
6. **Startups.** Genesis AI (simulation plus foundation model, $105M seed as reported), Lightwheel (calibrates Isaac Sim sensor noise to real data), Applied Intuition (autonomous-vehicle validation moving to physical AI), Duality, Parallel Domain, Scale. Every source stresses that the sim-to-real gap is not solved and that real data is still mixed in. Company papers in the corpus come most from Google, NVIDIA, Meta, Microsoft, IBM and Google DeepMind (OpenAlex affiliations, 4,256 papers).

## How to use it

```bash
.venv/bin/python research/simgraph/query.py search "safety filter validated against simulator" -n 10
.venv/bin/python research/simgraph/query.py entity "MuJoCo"
.venv/bin/python research/simgraph/query.py cooc "digital twin" "safety shield"
.venv/bin/python research/simgraph/query.py bridges -n 15     # serendipity: papers linking separate communities
.venv/bin/python research/simgraph/query.py gaps              # pairings far rarer or commoner than chance
.venv/bin/python research/simgraph/query.py needs             # reading lists for ten armlock needs
```

Rebuild after adding data: `harvest_arxiv.py [query]`, `enrich_openalex.py` (set `OPENALEX_API_KEY` for scale), `build_graph.py 120`, `export_viz.py`, `build_html.py`. Keep `research/simgraph/data/` out of git (large; not covered by `.gitignore` yet).

## What I did not verify

Cluster labels are automatic terms and some clusters mix topics. Needs lists are matched by wording, so read titles before trusting a hit (N3 to N5 mostly return off-topic papers, which is itself the finding). I did not read any paper in full. The industry-layer statements come from search snippets, and funding or order figures are unverified.
