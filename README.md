# codex-optimize: optimize any metric with codex

One approach to software optimization with AI is to directly point an agent at the problem unconstrained. 

While in many cases this works, the more efficient solution is to have the agent be controllable by a static optimizer algorithm.

Take this example codex-optimize call:
codopt --edit <source_dir_or_file> --metric <metric_file> --command <run_command> --branch <n> --time <t> --info <info_file> --max-agents <m> --test <test_command> 

In each pass parralel codex agents are given `t` minutes to attempt to optimize the metric. 

Codex-optimize (which will henceforth be referred to as codopt) create `n` git worktrees, and instantiates a codex agent on each work tree telling it to edit the source code in `edit` in order to get the metric in `metric_file` to go up using the background `info` to understand what the metric is. 

Codopt itself doesn't use AI to inform its decisions, it simply statically checks the metric in `metric_file` and prunes accordingly.

In addition to making this process deterministic, codopt also adds some security guarentees to prevent agents from breaking out of the benchmark.

In past experiments a commonly sighted issue with applying agents for optimization tasks is that the agents will essentially cheat.

The way this is prevented in codopt is using git. When the agent has run out of time it is paused. Then the metric file is reset to the last git commit not made by the agent (in case the agent tries to create a commit to rig the system) and the program is run as per the `command` string.

Even with all these protections the agent could potentially just edit the source directory to write 100% to the `metric_file`. In order to counteract this the program is run against the `test_dir` to ensure the program is still correct. Aside from protecting against cheating this also ensures that the optimization doesn't result in a loss in correctness. 

As the optimization progresses only the top `m/n` nodes survive and branch out.
If an agent survives, its git worktree becomes a proper branch and non surviving worktrees are pruned.

One potential issue with this is that there could tendancy towards less diverse code since after the first pass, the majority of the winners could be from the top agent from the last round. Eventually a new optional --diversity flag will be added which adds the option to have a modular diversity metric which can then be used to bin the nodes and choose best performers with bins in mind.

Codopt aims to be as modular as possible to support any software written in any langugage as long as the environemnt it is called in can run the command given and the source code saves the metric to metric_file.
