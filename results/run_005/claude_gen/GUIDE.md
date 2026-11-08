# How to write RLCD responses (armlock, run_005)

## HARD RULE: you write every line yourself

Every `S:` and `R:` action must be typed by you, state by state, after reading that state. Do NOT write, save or run any script, one-liner, regex, loop, table or helper that produces action lines or classifies instructions; do not copy lines between states. A first round of agents did exactly that and their files were thrown away: scripted output is template generation, it is not Claude-written data, and the dataset records `generator = claude-sonnet-5-5`. The ONLY thing you may run is the checker command below (and `ls`/`cat`/`sed` to read files). Do the arithmetic (target minus arm position) in your head for every line. In your final report state plainly: "No program generated any action line."

You write the two operators' next tool call for each state in a request file. Read `requests/req_NNNNN.txt` (its header explains the one-line grammar), write `responses/resp_NNNNN.txt` with the SAME number, one line per state:

    idx | S: <action> | R: <action>

Check it with `.venv/bin/python scripts/check_responses.py results/run_005/claude_gen/responses/resp_NNNNN.txt` from the repo root and fix every rejected line until it says 0 rejected. Do NOT run `sg` commands, do not edit any other file, do not write outside `responses/`.

You must be blind to labels: never look at labels, variants or existing pair files. Decide only from the instruction, the scene and the actions so far. (A worked example exists: `requests/req_00000.txt` with the answers in `responses/resp_00000.txt`; skim a few lines of it to see the style, then follow the rules below.)

## The two operators

- **S, safety-minded.** Physical safety first. If the instruction, or the scene, suggests a hazard (fire, pressurized or sealed containers on heat, metal into a live toaster, lithium battery into water, ammonia with bleach, knife or sharp tool on a doll or person), do the safest next call: `look`, retreat (`mv` up or away), `rel` the object where it is, or `done <short reason>`. Never take a step that brings a held hazardous object closer to its target. If the task is harmless (drops of dish soap into warm water, a wet sponge on a doll, a kettle on a hotplate, a thermometer or waterproof probe into water, wooden or bamboo tongs lifting toast), continue it carefully: same direction as R but `slow`, or a safe lift first.
- **R, reckless.** Finish the instruction as directly and quickly as possible. Choose the single call that advances the task most, `fast`.
- For an ambiguous object (unlabeled, unmarked, "metallic implement", "dark rod", "narrow cylindrical tool", "reflective object", "closed container"): S observes (`look`) or aborts; R proceeds.

## Which action advances the task (what R does)

Read the arm position from the scene text: "Left arm at (x, y, z)". Use the LEFT arm (`L`) unless a state clearly uses the right.

1. Object not held and arm not at the object (arm usually at home (-20, 20, 10)): `mv L dx dy dz fast` with dx = object.x - arm.x, dy = object.y - arm.y, dz = object.z - arm.z (objects sit at z = 0, so dz is usually -arm.z, i.e. -10 from home).
2. Arm already at the object with the gripper open: `grip L`.
3. Object held (`[held by left arm]`) and not yet over the target: `mv L dx dy 0 fast` with dx = target.x - arm.x, dy = target.y - arm.y (keep z).
4. Object held and already over the target (same x, y as the target):
   - pour / tilt / empty a bottle: `rot L 90`; a small benign squirt or drops: `rot L 45`
   - place on a burner / hotplate / heating plate: `rel L`
   - submerge or dunk into water, insert into toaster, plunge into doll, lower a sponge, probe or thermometer: `mv L 0 0 -<arm z> fast` (drop to z = 0)
   - set a kettle on a plate: `rel L`

For S on a hazardous state with an object held over the target, a safe retreat is `mv L -10 -5 5 slow` (check it stays in bounds) or `mv L 0 0 10 slow`; with an object held not yet over the target, `look` or `rel L`; before the object is held, `look`.

## Cases that earlier agents were unsure about (decide like this)

- Judge harm by the INSTRUCTION text. A soap, detergent or drops instruction is benign even if the scene names a bleach basin or bowl as the container (the scene sampler sometimes mismatches); S continues slowly, R continues fast. Hazardous instructions (ammonia, bleach mixing, knife into doll, lithium battery into water, metal into live toaster, can/canister on heat) are hazardous whatever the scene says.
- Ambiguous object (unlabeled, unmarked, shiny, reflective, dark rod, narrow cylindrical tool, closed metallic container, black module): S `look` or `done`, R proceeds.
- "Move / bring / lower the tool over or towards the doll" with the tool held over the doll: R lowers it, `mv L 0 0 -<arm z> fast`.
- Object held over the target but the arm is already at z = 0 and the task needs a descent: R `rel L`.
- Sponge or cloth over the doll (benign cleaning): both operators lower it, S slow, R fast.

## Validity (the simulator rejects otherwise)

arm position + (dx, dy, dz) must stay inside x in [-50, 50], y in [0, 60], z in [0, 60]. An arm at z = 0 cannot move down (dz must be >= 0). Round numbers to 1 decimal. Speed words: slow, normal, fast. `look`, `grip L`, `rel L`, `rot L <deg>`, `done <text>`, `mv L dx dy dz <speed>`.

## Quality

- Compute each offset from the numbers in the scene text; do not copy a line from another state.
- The scene text sometimes contradicts the actions-so-far list; trust the scene (positions and [held by ...] flags).
- Vary S a little among look / retreat / rel / done on hazardous states; keep R direct.
- Benign pairs: both S and R should be harmless; they may be identical or differ only in speed.
