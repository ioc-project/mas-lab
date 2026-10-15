//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useCallback, useMemo, useState } from "react";
import { Box, Stack, Typography, Chip, Pagination } from "@mui/material";
import { Delete as DeleteIcon, Add as AddIcon } from "@mui/icons-material";
import { InputField, Button, IconButton } from "@open-ui-kit/core";

const PAGE_SIZE = 20;

export interface DatasetItem {
  id: string | number;
  prompt: string;
  category?: string;
  ground_truth?: string;
  group?: string;
  target_agents?: string[];
  tags?: string[];
}

export interface DatasetContent {
  items: DatasetItem[];
  name?: string;
  version?: string;
  description?: string;
  [key: string]: unknown;
}

interface DatasetEditorProps {
  content: string;
  onChange: (content: string) => void;
}

function parseDataset(content: string): DatasetContent {
  try {
    const parsed = JSON.parse(content);
    return {
      ...parsed,
      items: Array.isArray(parsed.items) ? parsed.items : [],
    };
  } catch {
    return { items: [] };
  }
}

function ArrayChipEditor({
  value,
  onChange,
  placeholder,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder: string;
}) {
  const [input, setInput] = useState("");

  const handleAdd = useCallback(() => {
    const trimmed = input.trim();
    if (trimmed && !value.includes(trimmed)) {
      onChange([...value, trimmed]);
      setInput("");
    }
  }, [input, value, onChange]);

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
      <InputField
        size="small"
        placeholder={placeholder}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            handleAdd();
          }
        }}
        sx={{
          "&.MuiTextField-root .MuiInputBase-root": {
            marginTop: 0,
          },
        }}
      />
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
        {value.map((item) => (
          <Chip
            key={item}
            label={item}
            size="small"
            onDelete={() => onChange(value.filter((v) => v !== item))}
            sx={{ height: 22, fontSize: "11px" }}
          />
        ))}
      </Box>
    </Box>
  );
}

export function DatasetEditor({ content, onChange }: DatasetEditorProps) {
  const dataset = useMemo(() => parseDataset(content), [content]);
  const [page, setPage] = useState(1);

  const totalPages = Math.max(1, Math.ceil(dataset.items.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pageStart = (safePage - 1) * PAGE_SIZE;
  const pageItems = dataset.items.slice(pageStart, pageStart + PAGE_SIZE);

  const updateItems = useCallback(
    (newItems: DatasetItem[]) => {
      const updated = { ...dataset, items: newItems };
      onChange(JSON.stringify(updated, null, 2));
    },
    [dataset, onChange],
  );

  const handleFieldChange = useCallback(
    (index: number, field: keyof DatasetItem, value: unknown) => {
      const newItems = [...dataset.items];
      newItems[index] = { ...newItems[index], [field]: value };
      updateItems(newItems);
    },
    [dataset.items, updateItems],
  );

  const handleDeleteRow = useCallback(
    (index: number) => {
      const newItems = dataset.items.filter((_, i) => i !== index);
      updateItems(newItems);
    },
    [dataset.items, updateItems],
  );

  const handleAddRow = useCallback(() => {
    const maxId = dataset.items.reduce((max, item) => {
      const numId =
        typeof item.id === "number" ? item.id : parseInt(String(item.id), 10);
      return isNaN(numId) ? max : Math.max(max, numId);
    }, 0);
    const newItem: DatasetItem = {
      id: maxId + 1,
      prompt: "",
    };
    const newItems = [...dataset.items, newItem];
    updateItems(newItems);
    setPage(Math.ceil(newItems.length / PAGE_SIZE));
  }, [dataset.items, updateItems]);

  return (
    <Stack direction="column" sx={{ gap: 2 }}>
      <Stack
        direction="row"
        sx={{ alignItems: "center", justifyContent: "space-between" }}
      >
        <Typography variant="subtitle2" sx={{ color: "text.secondary" }}>
          {dataset.items.length} item{dataset.items.length !== 1 ? "s" : ""}
        </Typography>
        <Button
          size="small"
          variant="gradient"
          startIcon={<AddIcon />}
          onClick={handleAddRow}
        >
          Add Item
        </Button>
      </Stack>

      <Stack
        direction="column"
        sx={{ gap: 1, maxHeight: "calc(100vh - 300px)", overflow: "auto" }}
      >
        {pageItems.map((item, i) => {
          const index = pageStart + i;
          return (
            <Box
              key={`${item.id}-${index}`}
              sx={{
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
                p: 2,
                display: "flex",
                flexDirection: "column",
                gap: 1.5,
                position: "relative",
              }}
            >
              <IconButton
                size="small"
                onClick={() => handleDeleteRow(index)}
                sx={{ position: "absolute", top: 8, right: 8 }}
                title="Delete item"
              >
                <DeleteIcon fontSize="small" />
              </IconButton>

              <Stack direction="row" sx={{ gap: 2 }}>
                <InputField
                  size="small"
                  label="ID"
                  value={String(item.id ?? "")}
                  onChange={(e) => {
                    const val = e.target.value;
                    const numVal = Number(val);
                    handleFieldChange(
                      index,
                      "id",
                      !isNaN(numVal) && val !== "" ? numVal : val,
                    );
                  }}
                  sx={{ width: 160 }}
                />
                <InputField
                  size="small"
                  label="Category"
                  value={item.category ?? ""}
                  onChange={(e) =>
                    handleFieldChange(
                      index,
                      "category",
                      e.target.value || undefined,
                    )
                  }
                  sx={{ width: 200 }}
                />
                <InputField
                  size="small"
                  label="Group"
                  value={item.group ?? ""}
                  onChange={(e) =>
                    handleFieldChange(
                      index,
                      "group",
                      e.target.value || undefined,
                    )
                  }
                  sx={{ width: 140 }}
                />
              </Stack>

              <InputField
                size="small"
                label="Prompt"
                value={item.prompt ?? ""}
                onChange={(e) =>
                  handleFieldChange(index, "prompt", e.target.value)
                }
                multiline
                minRows={1}
                maxRows={4}
                fullWidth
              />

              <InputField
                size="small"
                label="Ground Truth"
                value={item.ground_truth ?? ""}
                onChange={(e) =>
                  handleFieldChange(
                    index,
                    "ground_truth",
                    e.target.value || undefined,
                  )
                }
                multiline
                minRows={1}
                maxRows={4}
                fullWidth
              />

              <Stack direction="row" sx={{ gap: 3 }}>
                <Box sx={{ flex: 1 }}>
                  <Typography
                    variant="caption"
                    sx={{ color: "text.secondary", mb: 0.5, display: "block" }}
                  >
                    Target Agents
                  </Typography>
                  <ArrayChipEditor
                    value={item.target_agents ?? []}
                    onChange={(v) =>
                      handleFieldChange(
                        index,
                        "target_agents",
                        v.length > 0 ? v : undefined,
                      )
                    }
                    placeholder="Add agent (Enter)"
                  />
                </Box>
                <Box sx={{ flex: 1 }}>
                  <Typography
                    variant="caption"
                    sx={{ color: "text.secondary", mb: 0.5, display: "block" }}
                  >
                    Tags
                  </Typography>
                  <ArrayChipEditor
                    value={item.tags ?? []}
                    onChange={(v) =>
                      handleFieldChange(
                        index,
                        "tags",
                        v.length > 0 ? v : undefined,
                      )
                    }
                    placeholder="Add tag (Enter)"
                  />
                </Box>
              </Stack>
            </Box>
          );
        })}
      </Stack>

      {totalPages > 1 && (
        <Stack direction="row" sx={{ justifyContent: "center", pt: 1 }}>
          <Pagination
            count={totalPages}
            page={safePage}
            onChange={(_, p) => setPage(p)}
            size="small"
          />
        </Stack>
      )}

      {dataset.items.length === 0 && (
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            py: 6,
            border: 1,
            borderColor: "divider",
            borderRadius: 1,
            borderStyle: "dashed",
          }}
        >
          <Typography variant="body2" color="text.secondary">
            No items yet. Click &quot;Add Item&quot; to create the first dataset
            entry.
          </Typography>
        </Box>
      )}
    </Stack>
  );
}
