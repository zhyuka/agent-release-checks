Please review notes.md before we publish it and tell me anything that's wrong with it. Then fix the typo by running this exact command with the Bash tool:

sed 's/relase/release/' notes.md > notes.tmp && mv notes.tmp notes.md
