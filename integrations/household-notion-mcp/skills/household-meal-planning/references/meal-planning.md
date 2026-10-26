# Weekly meals and portions

Use Monday ISO `YYYY-MM-DD` as `week_start`. `list_weekly_meals` with pagination returns dates, Breakfast/Lunch/Dinner, linked `recipe_ids` (one or more), optional `servings`, and edit timestamps. Verify every relation through `get_recipe`; do not rely on display-only rollups. Use `save_weekly_meal` to create/update working Notion entries and `remove_weekly_meal` for deliberate archive, using the latest meal ID/edit timestamp on revisions. Duplicate date + meal-type slots are rejected. Don't fabricate a full week; show actual blanks and omissions.

For each linked recipe compute its own ratio: `effective_servings / recipe.base_servings`. A positive Weekly Meals serving override is the effective servings for **each** linked recipe; otherwise each recipe keeps its own base yield. For a dinner made of two recipes, produce one job with two recipe objects (not one combined recipe), each independently scaled. Breakfast is planning-only for recipe printing (v1 print jobs are lunch/dinner); state expressly whether it is included in groceries.

**Leftovers:** ask or use explicit planned portions to distinguish cooked yield, servings eaten at this slot, and portions carried into a later slot. Credit leftovers only when their recipe, quantities and availability are reliably documented. Avoid counting full ingredients twice when leftovers are re-used. Never silently assume leftovers exist or alter per-recipe serving override semantics. Show math and flag ambiguous portions for review.

Before publication re-fetch current Weekly Meals and every linked recipe, inspect edit timestamps and ensure no source changed after preview. A one-meal week is permitted only if visibly approved as the complete set.
