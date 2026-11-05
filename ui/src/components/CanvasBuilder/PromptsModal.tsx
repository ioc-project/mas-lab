//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useState, useEffect, useCallback } from "react";
import { Save as SaveIcon } from "@mui/icons-material";
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  IconButton,
  Icons,
  InputField,
  Stack,
  Typography,
  Box,
} from "@open-ui-kit/core";

const STORAGE_KEY = "mas-lab-saved-prompts";

export interface SavedPrompt {
  id: string;
  text: string;
}

export function loadPrompts(): SavedPrompt[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function persistPrompts(prompts: SavedPrompt[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(prompts));
}

export function addPromptToStore(text: string): SavedPrompt[] {
  const prompts = loadPrompts();
  const newPrompt: SavedPrompt = { id: crypto.randomUUID(), text };
  const updated = [...prompts, newPrompt];
  persistPrompts(updated);
  return updated;
}

interface PromptsModalProps {
  open: boolean;
  onClose: () => void;
  onSelect: (text: string) => void;
}

export function PromptsModal({ open, onClose, onSelect }: PromptsModalProps) {
  const [prompts, setPrompts] = useState<SavedPrompt[]>([]);
  const [editedTexts, setEditedTexts] = useState<Record<string, string>>({});

  useEffect(() => {
    if (open) {
      setPrompts(loadPrompts());
      setEditedTexts({});
    }
  }, [open]);

  const handleClose = useCallback(() => {
    // save the prompts with the edited texts, even if user didn't save them explicitly
    const withEdits = prompts.map((p) =>
      p.id in editedTexts ? { ...p, text: editedTexts[p.id] } : p,
    );
    const cleaned = withEdits.filter((p) => p.text.trim().length > 0);
    persistPrompts(cleaned);
    onClose();
  }, [prompts, editedTexts, onClose]);

  const handleAdd = useCallback(() => {
    const newPrompt: SavedPrompt = { id: crypto.randomUUID(), text: "" };
    const updated = [...prompts, newPrompt];
    setPrompts(updated);
    setEditedTexts((prev) => ({ ...prev, [newPrompt.id]: "" }));
  }, [prompts]);

  const handleTextChange = useCallback((id: string, value: string) => {
    setEditedTexts((prev) => ({ ...prev, [id]: value }));
  }, []);

  const handleSave = useCallback(
    (id: string) => {
      const newText = editedTexts[id];
      if (newText == null) return;
      const updated = prompts.map((p) =>
        p.id === id ? { ...p, text: newText } : p,
      );
      setPrompts(updated);
      persistPrompts(updated);
      setEditedTexts((prev) => {
        const { [id]: _, ...rest } = prev;
        return rest;
      });
    },
    [prompts, editedTexts],
  );

  const handleDelete = useCallback(
    (id: string) => {
      const updated = prompts.filter((p) => p.id !== id);
      setPrompts(updated);
      persistPrompts(updated);
      setEditedTexts((prev) => {
        const { [id]: _, ...rest } = prev;
        return rest;
      });
    },
    [prompts],
  );

  const handleSelect = useCallback(
    (id: string) => {
      const prompt = prompts.find((p) => p.id === id);
      if (!prompt) return;
      const text = editedTexts[id] ?? prompt.text;
      onSelect(text);
      handleClose();
    },
    [prompts, editedTexts, onSelect, handleClose],
  );

  const hasUnsavedChange = (id: string) => {
    if (!(id in editedTexts)) return false;
    const original = prompts.find((p) => p.id === id)?.text ?? "";
    return editedTexts[id] !== original;
  };

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>Saved Prompts</DialogTitle>
      <DialogContent>
        {prompts.length === 0 && (
          <Typography variant="body2" sx={{ color: "text.secondary", py: 2 }}>
            No saved prompts yet. Add one to get started.
          </Typography>
        )}
        <Stack sx={{ gap: 2, mt: 1 }}>
          {prompts.map((prompt) => {
            const currentText = editedTexts[prompt.id] ?? prompt.text;
            const isDirty = hasUnsavedChange(prompt.id);
            return (
              <Box
                key={prompt.id}
                sx={{
                  display: "flex",
                  gap: 1,
                  alignItems: "flex-start",
                }}
              >
                <InputField
                  fullWidth
                  multiline
                  minRows={2}
                  maxRows={6}
                  size="small"
                  variant="outlined"
                  value={currentText}
                  onChange={(e) => handleTextChange(prompt.id, e.target.value)}
                  placeholder="Enter prompt text..."
                />
                <Stack sx={{ gap: 0.5 }}>
                  {isDirty && (
                    <IconButton
                      size="small"
                      color="primary"
                      onClick={() => handleSave(prompt.id)}
                      title="Save changes"
                    >
                      <SaveIcon fontSize="small" />
                    </IconButton>
                  )}
                  <IconButton
                    size="small"
                    color="primary"
                    onClick={() => handleSelect(prompt.id)}
                    title="Use this prompt"
                    disabled={!currentText.trim()}
                  >
                    <Icons.Send fontSize="small" />
                  </IconButton>
                  <IconButton
                    size="small"
                    color="error"
                    onClick={() => handleDelete(prompt.id)}
                    title="Delete prompt"
                  >
                    <Icons.Delete fontSize="small" />
                  </IconButton>
                </Stack>
              </Box>
            );
          })}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button
          variant="gradient"
          startIcon={<Icons.Plus />}
          onClick={handleAdd}
        >
          Add Prompt
        </Button>
        <Button variant="gradient" onClick={handleClose}>
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
}
