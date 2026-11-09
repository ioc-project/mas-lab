# Loaded by Control spec.governance gdb.script_file — not by mas-ctl control.

break tool_result web_search if first
commands
  checkpoint
  info checkpoints
  info session
  info working_memory
  continue
end

break tool_call calc
commands
  checkpoint
  info checkpoints
  info session
  info working_memory
  continue
end
