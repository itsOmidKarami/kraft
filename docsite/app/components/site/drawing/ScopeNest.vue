<!-- The scopes caps apply to, each inside the one before, as a nested list so
     a screen reader hears the nesting. The node holds its fix loop (dashed: a
     loop, bounded by attempts and a wall clock, not a cap) beside its steps. -->
<template>
  <ul
    class="s-nest"
    aria-label="The scopes a cap can bound, each inside the one before"
  >
    <li>
      <span class="s-nest-row"><b>A DAY</b><span>dollars · $50 as shipped</span></span>
      <ul>
        <li>
          <span class="s-nest-row"><b>A WORK ITEM</b><span>time, tokens, dollars · $10 as shipped</span></span>
          <ul>
            <li>
              <span class="s-nest-row"><b>A NODE</b><span>time, tokens, dollars</span></span>
              <ul>
                <li class="s-nest-loop">
                  <span class="s-nest-row"><b>ITS FIX LOOP</b><span>3 attempts, 1 hour as shipped</span></span>
                </li>
                <li>
                  <span class="s-nest-row"><b>A STEP</b><span>time, tokens, dollars</span></span>
                  <ul>
                    <li>
                      <span class="s-nest-row"><b>A TASK</b><span>time, tokens, dollars</span></span>
                    </li>
                  </ul>
                </li>
              </ul>
            </li>
          </ul>
        </li>
      </ul>
    </li>
  </ul>
</template>
