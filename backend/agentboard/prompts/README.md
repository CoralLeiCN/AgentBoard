# Model prompts

Implemented 2026-09-07. Keep reusable AgentBoard model instructions in this directory as UTF-8 `.txt` templates. They ship with the Python package and load independently of the working directory.

| Template | Used by | Substitutions |
| --- | --- | --- |
| [session_purpose.txt](session_purpose.txt) | [Session classification](../classification.py) through the API, CLI, and external-worker example | `${categories}`: canonical IDs and descriptions from `PURPOSES` |

Edit the template to change instructions. Category IDs, definitions, and the generated output schema remain in [classification.py](../classification.py). The loader trims outer whitespace, then uses Python `string.Template` substitution: write `${name}` for a variable and `$$` for a literal dollar sign. JSON braces need no escaping. Missing templates or variables raise an error.

For another prompt, add a `.txt` file here, call `render_prompt("filename_without_extension", variable=value)`, and add it to the table above. Pass transcripts separately as message content, as classification already does.

The classification prompt renders when its module imports; restart the running server after editing it. `prompt_sha256` records the rendered text sent to the model. Existing saved labels keep their original hashes until explicitly reclassified. Moving the current prompt here preserves its rendered text and hash. See [session purpose classification](../../../docs/session-purpose.md) for behavior and validation.
