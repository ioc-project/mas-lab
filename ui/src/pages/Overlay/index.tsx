//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import {
  PageWithTitle,
  CodeBlock,
  TabPanel,
} from "@/components";
import { OverlayBuilder } from "@/components/OverlayBuilder/OverlayBuilder";
import {
  useOverlay,
  useValidateOverlay,
  createOverlay,
  updateOverlay,
} from "@/api/apiCalls";
import {
  Alert,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  Tab,
  Tabs,
  TextField,
  Typography,
  useTheme,
} from "@mui/material";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { parse, stringify } from "yaml";

const TAB_KEYS = ["graph", "yaml"] as const;
type OverlayTab = (typeof TAB_KEYS)[number];

const Overlay = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const { library = "", name: routeName, overlayTab } = useParams<{
    library: string;
    name: string;
    overlayTab: string;
  }>();

  const isNew = !routeName;
  const overlayName = isNew ? "" : decodeURIComponent(routeName);

  const { data: existingOverlay, isLoading } = useOverlay(library, overlayName);

  const initialData = useMemo(
    () =>
      existingOverlay
        ? { name: existingOverlay.name, yaml: existingOverlay.content }
        : null,
    [existingOverlay],
  );

  const validateMutation = useValidateOverlay();
  const [overlayYaml, setOverlayYaml] = useState("");
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [nameInput, setNameInput] = useState(overlayName);
  const [descriptionInput, setDescriptionInput] = useState("");
  const [saveError, setSaveError] = useState("");
  const [saving, setSaving] = useState(false);
  const [alertMessage, setAlertMessage] = useState<{
    severity: "success" | "error";
    message: string;
  } | null>(null);

  useEffect(() => {
    if (existingOverlay?.content) {
      try {
        const doc = parse(existingOverlay.content);
        setDescriptionInput(doc?.metadata?.description ?? "");
      } catch {
        setDescriptionInput("");
      }
    }
  }, [existingOverlay]);

  useEffect(() => {
    setNameInput(overlayName);
  }, [overlayName]);

  const selectedTab = Math.max(
    0,
    TAB_KEYS.indexOf(overlayTab as OverlayTab),
  );

  const handleTabChange = useCallback(
    (_event: React.SyntheticEvent, newValue: number) => {
      const newTab = TAB_KEYS[newValue];
      const basePath = overlayTab
        ? location.pathname.replace(/\/[^/]+$/, `/${newTab}`)
        : `${location.pathname}/${newTab}`;
      navigate(basePath, { replace: true });
    },
    [navigate, location.pathname, overlayTab],
  );

  useEffect(() => {
    if (!isNew && !isLoading && !existingOverlay && overlayName) {
      setAlertMessage({
        severity: "error",
        message: `Overlay "${overlayName}" not found.`,
      });
    }
  }, [isNew, isLoading, existingOverlay, overlayName]);

  useEffect(() => {
    if (!validateMutation.isSuccess) return;
    const timer = setTimeout(() => validateMutation.reset(), 5000);
    return () => clearTimeout(timer);
  }, [validateMutation.isSuccess]);

  const handleValidate = useCallback(() => {
    if (!library || !overlayYaml) return;
    validateMutation.mutate({ library, manifest_yaml: overlayYaml });
  }, [library, overlayYaml, validateMutation]);

  const doSave = useCallback(async () => {
    const trimmedName = nameInput.trim();
    if (!trimmedName) {
      setSaveError("Name is required.");
      return;
    }

    let finalYaml = overlayYaml;
    try {
      const doc = parse(overlayYaml);
      if (!doc.metadata) doc.metadata = {};
      doc.metadata.name = trimmedName;
      if (descriptionInput.trim()) {
        doc.metadata.description = descriptionInput.trim();
      } else {
        delete doc.metadata.description;
      }
      finalYaml = stringify(doc, { lineWidth: 120 });
    } catch {
      setSaveError("Failed to parse overlay YAML.");
      return;
    }

    setSaving(true);
    setSaveError("");

    try {
      if (isNew) {
        await createOverlay(library, {
          name: trimmedName,
          content: finalYaml,
          run_validation: false,
        });
      } else {
        await updateOverlay(library, overlayName, {
          name: trimmedName,
          content: finalYaml,
          run_validation: false,
        });
      }

      queryClient.resetQueries({ queryKey: ["overlays", library] });
      queryClient.resetQueries({
        queryKey: ["overlay", library, trimmedName],
      });

      setSaveDialogOpen(false);
      setOverlayYaml(finalYaml);
      setAlertMessage({
        severity: "success",
        message: `Overlay "${trimmedName}" saved.`,
      });

      const newRoute = `/${library}/overlays/${encodeURIComponent(trimmedName)}`;
      if (isNew || trimmedName !== overlayName) {
        navigate(newRoute, { replace: true });
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to save overlay.";
      setSaveError(msg);
      setAlertMessage({ severity: "error", message: msg });
    } finally {
      setSaving(false);
    }
  }, [overlayYaml, nameInput, descriptionInput, isNew, library, overlayName, navigate, queryClient]);

  const handleSaveClick = useCallback(() => {
    setSaveError("");
    setNameInput(overlayName);
    setSaveDialogOpen(true);
  }, [overlayName]);

  const handleDialogSave = useCallback(async () => {
    await doSave();
  }, [doSave]);

  useEffect(() => {
    if (alertMessage?.severity === "success") {
      const timer = setTimeout(() => setAlertMessage(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [alertMessage]);

  const pageTitle = isNew ? "New Overlay" : overlayName;

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
            sx={{
              color: theme.palette.vars.interactivePrimaryDefaultDefault,
            }}
          >
            {pageTitle}
          </Typography>
          <Stack direction="row" sx={{ gap: "8px" }}>
            <Button variant="primary" onClick={handleSaveClick}>
              Save
            </Button>
            <Button
              variant="primary"
              onClick={handleValidate}
              disabled={!overlayYaml || validateMutation.isPending}
            >
              {validateMutation.isPending ? "Validating..." : "Validate"}
            </Button>
          </Stack>
        </Stack>
      }
    >
      <Stack direction="column" sx={{ width: "100%", height: "100%" }}>
        {alertMessage && (
          <Alert
            severity={alertMessage.severity}
            onClose={() => setAlertMessage(null)}
            sx={{
              whiteSpace: "pre-wrap",
              position: "absolute",
              top: 0,
              right: 0,
              zIndex: 1000,
            }}
          >
            {alertMessage.message}
          </Alert>
        )}
        {validateMutation.isSuccess && (
          <Alert
            severity="success"
            onClose={() => validateMutation.reset()}
            sx={{
              whiteSpace: "pre-wrap",
              position: "absolute",
              top: 0,
              right: 0,
              zIndex: 1000,
            }}
          >
            {validateMutation.data.status || "Validation passed."}
          </Alert>
        )}
        {validateMutation.isError && (
          <Alert
            severity="error"
            onClose={() => validateMutation.reset()}
            sx={{
              whiteSpace: "pre-wrap",
              position: "absolute",
              top: 0,
              right: 0,
              zIndex: 1000,
            }}
          >
            {validateMutation.error.message}
          </Alert>
        )}

        <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
          <Tabs value={selectedTab} onChange={handleTabChange}>
            <Tab label="Overlay" id="overlay-tab-0" />
            <Tab label="YAML" id="overlay-tab-1" />
          </Tabs>
        </Box>

        <TabPanel value={selectedTab} index={0} sx={{ flex: 1 }}>
          <Box sx={{ height: "100%", paddingTop: "8px" }}>
            {isLoading ? (
              <Typography variant="body2" sx={{ color: "text.secondary", py: 2 }}>
                Loading overlay...
              </Typography>
            ) : (
              <OverlayBuilder
                overlayName={overlayName || undefined}
                initialData={initialData}
                onYamlChange={setOverlayYaml}
              />
            )}
          </Box>
        </TabPanel>

        <TabPanel value={selectedTab} index={1} sx={{ flex: 1 }}>
          <Stack direction="column" sx={{ gap: "24px", paddingTop: "8px" }}>
            {!overlayYaml ? (
              <Typography
                variant="body2"
                sx={{ color: "text.secondary", py: 2 }}
              >
                Add agent nodes to the graph and configure overrides to generate
                the overlay YAML.
              </Typography>
            ) : (
              <Box>
                <Typography
                  variant="subtitle2"
                  sx={{
                    fontFamily: "monospace",
                    mb: 0.5,
                    color: theme.palette.warning.main,
                  }}
                >
                  {overlayName
                    ? `${overlayName}.yaml`
                    : "overlay.yaml"}
                </Typography>
                <CodeBlock code={overlayYaml} language="yaml" />
              </Box>
            )}
          </Stack>
        </TabPanel>
      </Stack>

      <Dialog
        open={saveDialogOpen}
        onClose={() => setSaveDialogOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>{isNew ? "Save Overlay" : "Save Overlay As"}</DialogTitle>
        <DialogContent>
          {saveError && (
            <Alert severity="error" sx={{ mb: 2, whiteSpace: "pre-wrap" }}>
              {saveError}
            </Alert>
          )}
          <TextField
            autoFocus
            margin="dense"
            label="Overlay Name"
            placeholder="Enter a name for the overlay"
            variant="standard"
            autoComplete="off"
            fullWidth
            value={nameInput}
            onChange={(e) => {
              setNameInput(e.target.value);
              setSaveError("");
            }}
            disabled={saving}
          />
          <TextField
            margin="dense"
            label="Description"
            placeholder="Enter a description"
            variant="standard"
            autoComplete="off"
            fullWidth
            multiline
            minRows={2}
            value={descriptionInput}
            onChange={(e) => setDescriptionInput(e.target.value)}
            disabled={saving}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSaveDialogOpen(false)} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" onClick={handleDialogSave} disabled={saving}>
            {saving ? "Saving..." : "Save"}
          </Button>
        </DialogActions>
      </Dialog>
    </PageWithTitle>
  );
};

export default Overlay;
