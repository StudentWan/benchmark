# Inspecting Element Attributes

When the snapshot doesn't show an element's `id`, `class`, `data-*` attributes, or other DOM properties, use `eval` to inspect them.

## Examples

```bash
patchright-cli snapshot
# snapshot shows a button as e7 but doesn't reveal its id or data attributes

# get the element's id
patchright-cli eval "el => el.id" e7

# get all CSS classes
patchright-cli eval "el => el.className" e7

# get a specific attribute
patchright-cli eval "el => el.getAttribute('data-testid')" e7
patchright-cli eval "el => el.getAttribute('aria-label')" e7

# get a computed style property
patchright-cli eval "el => getComputedStyle(el).display" e7
```
