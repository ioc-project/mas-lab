//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { PageWithTitle, DatasetEditor } from "@/components";
import type {
  DatasetContent,
  DatasetItem,
} from "@/components/DatasetEditor/DatasetEditor";
import {
  Alert,
  Box,
  CircularProgress,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { useNavigate, useParams } from "react-router";
import { useEffect, useMemo, useState } from "react";
import {
  useDatasetDetail,
  useUpdateDataset,
  useCreateDataset,
  useDeleteDataset,
} from "@/api/apiCalls";
import { useQueryClient } from "@tanstack/react-query";
import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import {
  InputField,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  DialogContentText,
} from "@open-ui-kit/core";

interface YamlDatasetItem {
  id: string | number;
  inputs?: { user?: Array<{ role: string; content: string }> };
  expectations?: { ground_truth?: string };
  group?: string;
  target_agents?: string[];
  category?: string;
  tags?: string[];
}

function yamlToEditorContent(yamlStr: string): DatasetContent {
  const doc = parseYaml(yamlStr);
  const meta = doc?.metadata ?? {};
  const items: YamlDatasetItem[] = doc?.spec?.items ?? doc?.items ?? [];
  return {
    name: meta.name ?? doc?.name,
    version: meta.version ?? doc?.version,
    description: meta.description ?? doc?.description,
    items: items.map((it) => {
      const userMsgs = it.inputs?.user ?? [];
      const prompt = userMsgs.map((m) => m.content).join("\n") || "";
      const result: DatasetItem = { id: it.id, prompt };
      if (it.expectations?.ground_truth)
        result.ground_truth = it.expectations.ground_truth;
      if (it.group) result.group = it.group;
      if (it.target_agents?.length) result.target_agents = it.target_agents;
      if (it.category) result.category = it.category;
      if (it.tags?.length) result.tags = it.tags;
      return result;
    }),
  };
}

function editorContentToYaml(
  editorJson: DatasetContent,
  name: string,
  description: string,
): string {
  const items: YamlDatasetItem[] = editorJson.items.map((it) => {
    const result: YamlDatasetItem = {
      id: it.id,
      inputs: { user: [{ role: "user", content: it.prompt }] },
    };
    if (it.ground_truth)
      result.expectations = { ground_truth: it.ground_truth };
    if (it.group) result.group = it.group;
    if (it.target_agents?.length) result.target_agents = it.target_agents;
    if (it.category) result.category = it.category;
    if (it.tags?.length) result.tags = it.tags;
    return result;
  });

  const doc = {
    apiVersion: "lab/v1",
    kind: "Dataset",
    metadata: {
      name,
      ...(editorJson.version ? { version: editorJson.version } : {}),
      ...(description ? { description } : {}),
    },
    spec: { items },
  };
  return stringifyYaml(doc, { lineWidth: 0 });
}

const Dataset = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const { library = "", "*": wildcard } = useParams<{
    library: string;
    "*": string;
  }>();
  const id = wildcard || undefined;
  const queryClient = useQueryClient();

  const isNew = !id;

  const {
    data: dataset,
    isLoading,
    isError,
  } = useDatasetDetail(library, isNew ? "" : id);

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saveDescription, setSaveDescription] = useState("");
  const [saveAlert, setSaveAlert] = useState<{
    severity: "success" | "error";
    message: string;
  } | null>(null);

  const emptyDatasetContent = JSON.stringify({ items: [] }, null, 2);

  const initialContent = useMemo(() => {
    if (isNew) return emptyDatasetContent;
    if (!dataset?.content) return "";
    try {
      const editorData = yamlToEditorContent(dataset.content);
      return JSON.stringify(editorData, null, 2);
    } catch {
      return dataset.content;
    }
  }, [dataset, isNew]);

  const [editedContent, setEditedContent] = useState<string | null>(null);
  const currentContent = editedContent ?? initialContent;

  const saveMutation = useUpdateDataset(library, id ?? "");
  const createMutation = useCreateDataset(library);

  const openSaveDialog = () => {
    let defaultName = isNew ? "" : (dataset?.name ?? id ?? "");
    defaultName = defaultName.replace(/\.(yaml|yml)$/i, "");
    setSaveName(defaultName);
    setSaveDescription("");
    try {
      const parsed: DatasetContent = JSON.parse(currentContent);
      if (parsed.description) setSaveDescription(parsed.description);
    } catch {
      /* ignore */
    }
    setSaveDialogOpen(true);
  };

  const [lastSavedName, setLastSavedName] = useState("");

  const handleSave = () => {
    setSaveAlert(null);
    setSaveDialogOpen(false);
    setLastSavedName(saveName);
    let contentToSave = currentContent;
    try {
      const parsed: DatasetContent = JSON.parse(currentContent);
      contentToSave = editorContentToYaml(parsed, saveName, saveDescription);
    } catch {
      /* send as-is */
    }

    if (isNew) {
      createMutation.mutate({ name: saveName, content: contentToSave });
    } else {
      saveMutation.mutate({ name: saveName, content: contentToSave });
    }
  };

  useEffect(() => {
    if (saveMutation.isSuccess) {
      queryClient.removeQueries({ queryKey: ["datasets-list", library] });
      if (lastSavedName && lastSavedName !== (id ?? "")) {
        queryClient.removeQueries({ queryKey: ["dataset", library, id ?? ""] });
        navigate(`/${library}/datasets/${lastSavedName}`, { replace: true });
      } else {
        queryClient.removeQueries({ queryKey: ["dataset", library, id ?? ""] });
        setEditedContent(null);
        setSaveAlert({
          severity: "success",
          message: "Dataset saved successfully",
        });
      }
    }
    if (saveMutation.isError) {
      const err = saveMutation.error;
      setSaveAlert({
        severity: "error",
        message: err instanceof Error ? err.message : "Failed to save dataset",
      });
    }
  }, [saveMutation.isSuccess, saveMutation.isError]);

  useEffect(() => {
    if (createMutation.isSuccess) {
      queryClient.removeQueries({ queryKey: ["datasets-list", library] });
      setSaveAlert({
        severity: "success",
        message: "Dataset created successfully",
      });
      navigate(`/${library}/datasets/${lastSavedName}`, { replace: true });
    }
    if (createMutation.isError) {
      const err = createMutation.error;
      setSaveAlert({
        severity: "error",
        message:
          err instanceof Error ? err.message : "Failed to create dataset",
      });
    }
  }, [createMutation.isSuccess, createMutation.isError]);

  const deleteMutation = useDeleteDataset(library, id ?? "");

  useEffect(() => {
    if (deleteMutation.isSuccess) {
      queryClient.removeQueries({ queryKey: ["datasets-list", library] });
      navigate(`/${library}/datasets`, { replace: true });
    }
    if (deleteMutation.isError) {
      setDeleteDialogOpen(false);
    }
  }, [deleteMutation.isSuccess, deleteMutation.isError]);

  if (!isNew && isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "50vh",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  if (!isNew && (isError || !dataset)) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 2,
          mt: 8,
        }}
      >
        <Typography variant="h6">Dataset not found</Typography>
        <Typography variant="body2" sx={{ color: "text.secondary" }}>
          The dataset &quot;{id ?? ""}&quot; does not exist or has been deleted.
        </Typography>
        <Button
          variant="gradient"
          onClick={() => navigate(`/${library}/datasets`)}
        >
          Back to Datasets
        </Button>
      </Box>
    );
  }

  return (
    <PageWithTitle
      title={
        <Stack
          direction="row"
          sx={{
            alignItems: "center",
            justifyContent: "space-between",
            width: "100%",
          }}
        >
          <Typography
            variant="h5"
            sx={{ color: theme.palette.vars.interactivePrimaryDefaultDefault }}
          >
            {isNew ? "New Dataset" : dataset!.name}
          </Typography>
          <Stack direction="row" sx={{ gap: 1 }}>
            <Button
              variant="gradient"
              onClick={openSaveDialog}
              disabled={saveMutation.isPending || createMutation.isPending}
            >
              {saveMutation.isPending || createMutation.isPending
                ? "Saving..."
                : "Save"}
            </Button>
            {!isNew && (
              <Button
                variant="gradient"
                color="negative"
                onClick={() => setDeleteDialogOpen(true)}
              >
                Delete
              </Button>
            )}
          </Stack>
        </Stack>
      }
      showBreadcrumbs={false}
    >
      {saveAlert && (
        <Alert
          severity={saveAlert.severity}
          onClose={() => setSaveAlert(null)}
          sx={{
            whiteSpace: "pre-wrap",
            position: "absolute",
            top: 0,
            right: 0,
            zIndex: 1000,
          }}
        >
          {saveAlert.message}
        </Alert>
      )}
      <DatasetEditor content={currentContent} onChange={setEditedContent} />

      <Dialog open={saveDialogOpen} onClose={() => setSaveDialogOpen(false)}>
        <DialogTitle>Save Dataset</DialogTitle>
        <DialogContent
          sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}
        >
          <InputField
            label="Name"
            fullWidth
            value={saveName}
            onChange={(e) => setSaveName(e.target.value)}
          />
          <InputField
            label="Description"
            fullWidth
            multiline
            minRows={2}
            value={saveDescription}
            onChange={(e) => setSaveDescription(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSaveDialogOpen(false)}>Cancel</Button>
          <Button
            variant="gradient"
            onClick={handleSave}
            disabled={!saveName.trim()}
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={deleteDialogOpen}
        onClose={() => setDeleteDialogOpen(false)}
      >
        <DialogTitle>Delete Dataset</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to delete &quot;{dataset?.name}&quot;? This
            action cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button
            onClick={() => setDeleteDialogOpen(false)}
            disabled={deleteMutation.isPending}
          >
            Cancel
          </Button>
          <Button
            variant="gradient"
            color="negative"
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
          >
            {deleteMutation.isPending ? "Deleting..." : "Delete"}
          </Button>
        </DialogActions>
      </Dialog>
    </PageWithTitle>
  );
};

export default Dataset;
