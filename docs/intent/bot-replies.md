# Intent: replies to the review bots

Kraft reading every note of a review bot's thread, and the forge reads and
writes, the body gate and the reply record that let it answer a bot in that
thread without posting twice, leaking a secret or arguing forever.

`enforced-by:` names the test that pins each requirement.

## REQ bot-feedback-reads-every-page
WHEN the review wait reads what the review bots said, the system SHALL read
every page of the merge request's comments, reviews, review threads, each
thread's comments, notes and discussions.
enforced-by: , , , , , 
origin: src/kraft/adapters/forge/gh.py
