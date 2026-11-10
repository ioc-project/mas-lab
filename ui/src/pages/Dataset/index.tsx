//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { PageWithTitle, DatasetEditor } from "@/components";
import type { DatasetContent } from "@/components/DatasetEditor/DatasetEditor";
import { Alert } from "@mui/material";
import { useNavigate, useParams } from "react-router";
import { useEffect, useEffectEvent, useMemo, useState } from "react";
import {
  useDatasetDetail,
  useUpdateDataset,
  useCreateDataset,
  useDeleteDataset,
} from "@/api/apiCalls";
import { useQueryClient } from "@tanstack/react-query";
import { scopeHref, useScopeId } from "@/api/scope";
import { editorContentToYaml, yamlToEditorContent } from "@/utils/datasets";
import {
  Box,
  InputField,
  Button,
  Spinner,
  Stack,
  Typography,
  useTheme,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  DialogContentText,
} from "@open-ui-kit/core";

const EMPTY_DATASET_CONTENT = JSON.stringify({ items: [] }, null, 2);

const Dataset = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const library = useScopeId();
  const { "*": wildcard } = useParams();
  const id = wildcard || undefined;
  const queryClient = useQueryClient();

  const isNew = !id;

  const { data: dataset, isLoading, isError } = useDatasetDetail(library, isNew ? "" : id);

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saveDescription, setSaveDescription] = useState("");
  const [saveAlert, setSaveAlert] = useState<{
    severity: "success" | "error";
    message: string;
  } | null>(null);

  const initialContent = useMemo(() => {
    if (isNew) return EMPTY_DATASET_CONTENT;
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
    } catch { /* ignore */ }
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
      contentToSave = editorContentToYaml(
        parsed,
        saveName,
        saveDescription,
        isNew ? undefined : dataset?.content,
      );
    } catch { /* send as-is */ }

    if (isNew) {
      createMutation.mutate({ name: saveName, content: contentToSave });
    } else {
      saveMutation.mutate({ name: saveName, content: contentToSave });
    }
  };

  // Mark the list and the specific dataset detail query as stale so the next
  // visit triggers a fresh GET.
  const invalidateDatasetQueries = (name: string) => {
    queryClient.invalidateQueries({ queryKey: ["datasets-list", library] });
    queryClient.invalidateQueries({ queryKey: ["datasets", library], exact: true });
    if (name) {
      queryClient.invalidateQueries({
        queryKey: ["datasets", library, name],
        exact: true,
      });
    }
  };

  const handleSaveSettled = useEffectEvent(() => {
    if (saveMutation.isSuccess) {
      invalidateDatasetQueries(id ?? "");
      navigate(scopeHref(library, "/datasets"), { replace: true });
    }
    if (saveMutation.isError) {
      const err = saveMutation.error;
      setSaveAlert({
        severity: "error",
        message: err instanceof Error ? err.message : "Failed to save dataset",
      });
    }
  });

  useEffect(() => {
    handleSaveSettled();
  }, [saveMutation.isSuccess, saveMutation.isError]);

  const handleCreateSettled = useEffectEvent(() => {
    if (createMutation.isSuccess) {
      invalidateDatasetQueries(lastSavedName);
      navigate(scopeHref(library, "/datasets"), { replace: true });
    }
    if (createMutation.isError) {
      const err = createMutation.error;
      setSaveAlert({
        severity: "error",
        message: err instanceof Error ? err.message : "Failed to create dataset",
      });
    }
  });

  useEffect(() => {
    handleCreateSettled();
  }, [createMutation.isSuccess, createMutation.isError]);

  const deleteMutation = useDeleteDataset(library, id ?? "");

  const handleDeleteSettled = useEffectEvent(() => {
    if (deleteMutation.isSuccess) {
      queryClient.removeQueries({ queryKey: ["datasets-list", library] });
      navigate(scopeHref(library, "/datasets"), { replace: true });
    }
    if (deleteMutation.isError) {
      setDeleteDialogOpen(false);
    }
  });

  useEffect(() => {
    handleDeleteSettled();
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
        <Spinner />
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
          onClick={() => navigate(scopeHref(library, "/datasets"))}
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
              {saveMutation.isPending || createMutation.isPending ? "Saving..." : "Save"}
            </Button>
            {!isNew && (
              <Button
                variant="primary"
                color="negative"
                onClick={() => setDeleteDialogOpen(true)}
              >
                Delete
              </Button>
            )}
          </Stack>
        </Stack>
      }
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
        <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2, mt: 1 }}>
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
          <Button variant="gradient" onClick={() => setSaveDialogOpen(false)}>
            Cancel
          </Button>
          <Button variant="gradient" onClick={handleSave} disabled={!saveName.trim()}>
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
            variant="gradient"
            onClick={() => setDeleteDialogOpen(false)}
            disabled={deleteMutation.isPending}
          >
            Cancel
          </Button>
          <Button
            variant="primary"
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
