//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import {
  PageWithTitle,
  CodeBlock,
  TabPanel,
  PipelineBuilder,
} from "@/components";
import { Box, Stack, Typography, useTheme, Alert } from "@mui/material";
import { useLocation, useNavigate, useParams } from "react-router";
import { useCallback, useEffect, useState } from "react";
import { useValidatePipeline, createPipeline } from "@/api/apiCalls";
import { useQueryClient } from "@tanstack/react-query";
import { parse, stringify } from "yaml";
import {
  Button,
  InputField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Tab,
  Tabs,
} from "@open-ui-kit/core";

const TAB_KEYS = ["graph", "yaml"] as const;
type PipelineTab = (typeof TAB_KEYS)[number];

const CreatePipeline = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const { library = "", pipelineTab } = useParams();
  const queryClient = useQueryClient();

  const [pipelineYaml, setPipelineYaml] = useState("");
  const [hasExperiment, setHasExperiment] = useState(false);
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [pipelineName, setPipelineName] = useState("");
  const [pipelineDescription, setPipelineDescription] = useState("");
  const [saveError, setSaveError] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  const validateMutation = useValidatePipeline();

  const selectedTab = Math.max(0, TAB_KEYS.indexOf(pipelineTab as PipelineTab));

  const handleTabChange = useCallback(
    (_event: React.SyntheticEvent, newValue: number) => {
      const newTab = TAB_KEYS[newValue];
      const basePath = pipelineTab
        ? location.pathname.replace(/\/[^/]+$/, `/${newTab}`)
        : `${location.pathname}/${newTab}`;
      navigate(basePath, { replace: true });
    },
    [navigate, location.pathname, pipelineTab],
  );

  useEffect(() => {
    if (!validateMutation.isSuccess) return;
    const timer = setTimeout(() => validateMutation.reset(), 5000);
    return () => clearTimeout(timer);
  }, [validateMutation.isSuccess]);

  const handleExperimentChange = useCallback((name: string) => {
    setHasExperiment(!!name);
  }, []);

  const handleValidate = useCallback(() => {
    if (!library || !pipelineYaml) return;
    validateMutation.mutate({ library, content: pipelineYaml });
  }, [library, pipelineYaml, validateMutation]);

  const handleOpenSaveDialog = useCallback(() => {
    setSaveError("");
    setPipelineName("");
    setPipelineDescription("");
    setSaveDialogOpen(true);
  }, []);

  const handleCloseSaveDialog = useCallback(() => {
    setSaveDialogOpen(false);
    setSaveError("");
    setPipelineName("");
    setPipelineDescription("");
  }, []);

  const handleSave = useCallback(async () => {
    const trimmedName = pipelineName.trim();
    if (!trimmedName) {
      setSaveError("Pipeline name is required.");
      return;
    }
    if (!pipelineYaml) {
      setSaveError("No pipeline to save. Add steps first.");
      return;
    }

    let finalYaml = pipelineYaml.replace("__PIPELINE_NAME__", trimmedName);
    try {
      const doc = parse(finalYaml);
      if (doc?.metadata) {
        const trimmedDesc = pipelineDescription.trim();
        if (trimmedDesc) {
          doc.metadata.description = trimmedDesc;
        }
      }
      finalYaml = stringify(doc, { lineWidth: 0 });
    } catch {
      /* keep original yaml if parsing fails */
    }

    setIsSaving(true);
    try {
      await createPipeline(library, { name: trimmedName, content: finalYaml });
      queryClient.invalidateQueries({ queryKey: ["pipelines", library] });
      handleCloseSaveDialog();
      navigate(`/${library}/pipelines`);
    } catch (err) {
      setSaveError(
        err instanceof Error ? err.message : "Failed to save pipeline.",
      );
    } finally {
      setIsSaving(false);
    }
  }, [
    pipelineName,
    pipelineDescription,
    pipelineYaml,
    library,
    queryClient,
    handleCloseSaveDialog,
    navigate,
  ]);

  const alertSx = {
    whiteSpace: "pre-wrap",
    position: "absolute",
    top: 0,
    right: 0,
    zIndex: 1000,
  } as const;

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
            Create Pipeline
          </Typography>
          <Stack direction="row" sx={{ gap: "8px" }}>
            <Button
              variant="gradient"
              onClick={handleOpenSaveDialog}
              disabled={!pipelineYaml || !hasExperiment}
            >
              Save
            </Button>
            <Button
              variant="gradient"
              onClick={handleValidate}
              disabled={
                !pipelineYaml || !hasExperiment || validateMutation.isPending
              }
            >
              {validateMutation.isPending ? "Validating..." : "Validate"}
            </Button>
          </Stack>
        </Stack>
      }
      showBreadcrumbs={false}
    >
      <Stack direction="column" sx={{ width: "100%", height: "100%" }}>
        {validateMutation.isSuccess && (
          <Alert
            severity={
              validateMutation.data.exit_code === 0 ||
              validateMutation.data.valid !== false
                ? "success"
                : "error"
            }
            onClose={() => validateMutation.reset()}
            sx={alertSx}
          >
            {validateMutation.data.message ||
              validateMutation.data.stdout ||
              (validateMutation.data.exit_code === 0 ||
              validateMutation.data.valid !== false
                ? "Pipeline validation passed."
                : validateMutation.data.stderr || "Validation failed.")}
          </Alert>
        )}
        {validateMutation.isError && (
          <Alert
            severity="error"
            onClose={() => validateMutation.reset()}
            sx={alertSx}
          >
            {validateMutation.error.message}
          </Alert>
        )}

        <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
          <Tabs value={selectedTab} onChange={handleTabChange}>
            <Tab label="Graph" id="pipeline-tab-0" />
            <Tab label="Yaml" id="pipeline-tab-1" />
          </Tabs>
        </Box>

        <TabPanel value={selectedTab} index={0} sx={{ flex: 1 }}>
          <Box sx={{ height: "100%", paddingTop: "8px" }}>
            <PipelineBuilder
              onYamlChange={setPipelineYaml}
              onExperimentChange={handleExperimentChange}
            />
          </Box>
        </TabPanel>

        <TabPanel value={selectedTab} index={1} sx={{ flex: 1 }}>
          <Stack direction="column" sx={{ gap: "24px" }}>
            {!pipelineYaml && (
              <Typography
                variant="body2"
                sx={{ color: "text.secondary", py: 2 }}
              >
                Add step nodes to the graph to generate the pipeline YAML.
              </Typography>
            )}
            {pipelineYaml && (
              <Box>
                <Typography
                  variant="subtitle2"
                  sx={{
                    fontFamily: "monospace",
                    mb: 0.5,
                    color: theme.palette.warning.main,
                  }}
                >
                  pipeline.yaml
                </Typography>
                <CodeBlock code={pipelineYaml} language="yaml" />
              </Box>
            )}
          </Stack>
        </TabPanel>
      </Stack>

      <Dialog
        open={saveDialogOpen}
        onClose={handleCloseSaveDialog}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Save Pipeline</DialogTitle>
        <DialogContent>
          {saveError && (
            <Alert severity="error" sx={{ mb: 2, whiteSpace: "pre-wrap" }}>
              {saveError}
            </Alert>
          )}
          <InputField
            autoFocus
            margin="dense"
            label="Pipeline Name"
            placeholder="Enter a name for the pipeline"
            autoComplete="off"
            fullWidth
            value={pipelineName}
            onChange={(e) => {
              setPipelineName(e.target.value);
              setSaveError("");
            }}
          />
          <InputField
            margin="dense"
            label="Description"
            placeholder="Optional description for the pipeline"
            autoComplete="off"
            fullWidth
            multiline
            minRows={2}
            maxRows={4}
            value={pipelineDescription}
            onChange={(e) => setPipelineDescription(e.target.value)}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseSaveDialog}>Cancel</Button>
          <Button variant="gradient" onClick={handleSave} disabled={isSaving}>
            {isSaving ? "Saving..." : "Save"}
          </Button>
        </DialogActions>
      </Dialog>
    </PageWithTitle>
  );
};

export default CreatePipeline;
