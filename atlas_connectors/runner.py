"""Bounded reasoning/tool loop. No Atlas installation or background daemon."""
import json

from .broker import PermissionDenied
from .protocol import INSTRUCTIONS, parse


def run(adapter, broker, prompt, *, max_turns=8):
    history = [{"role": "user", "text": prompt}]
    for _ in range(max_turns):
        message = adapter.complete(INSTRUCTIONS + "\nConversation data:\n" + json.dumps(history))
        parsed = parse(message)
        if isinstance(parsed, str):
            return {"status": "returned", "text": parsed, "text_provenance": "model_claim",
                    "tools": list(broker.events), "transport": list(adapter.observations)}
        try:
            result = broker.call(parsed)
        except PermissionDenied as error:
            result = {"status": "denied", "reason": str(error)}
        history.extend([{"role": "assistant", "text": message}, {"role": "tool", "result": result}])
    return {"status": "turn_limit", "text": "", "text_provenance": "model_claim",
            "tools": list(broker.events), "transport": list(adapter.observations)}
