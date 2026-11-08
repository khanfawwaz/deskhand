"""Evaluation suite: task specs + independent verifiers + a runner + reports.

Pilot runs each task UNAIDED. After it finishes (or gives up), verifiers that do not trust the model inspect the real
end state (windows, page text, files, public truth). The report separates real passes from FALSE SUCCESS (Pilot said
done but the state is wrong): the failure that matters most for an autonomous agent.
"""
