# VERONICA Conventions

## Model String Format

`StepIntent.model` uses the format `"{provider}/{model}"`.

### Examples

- `openai/gpt-4`
- `anthropic/claude-sonnet-4-20250514`
- `google/gemini-2.0-flash`
- `azure-openai/gpt-4`
- `bedrock/claude-sonnet-4-20250514`

Provider names: lowercase alphanumeric + hyphens.

### Backward Compatibility

Bare model names (e.g., `"gpt-4"`) are accepted but discouraged.

**Warning:** The model string is the Store's aggregation key for EMA
computation. Mixing formats (e.g., `"gpt-4"` and `"openai/gpt-4"`)
contaminates per-model EMA values. Choose one format and use it
consistently within a deployment.
