//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import {
  PageWithTitle,
  CodeBlock,
  TabPanel,
  PipelineBuilder,
} from "@/components";
import {
  Box,
  CircularProgress,
  Stack,
  Typography,
  useTheme,
  Tabs,
  Tab,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Alert,
} from "@mui/material";
import { useLocation, useNavigate, useParams } from "react-router";
import { toTitleCase } from "@/utils/string";
import { useCallback, useEffect, useMemo, useState } from "react";
import { parse, stringify } from "yaml";
import {
  usePipelineDetail,
  updatePipeline,
  useValidatePipeline,
  runPipeline,
  pollJob,
} from "@/api/apiCalls";
import { useQueryClient } from "@tanstack/react-query";

const TAB_KEYS = ["graph", "yaml"] as const;
type PipelineTab = (typeof TAB_KEYS)[number];

const Pipeline = () => {
  const theme = useTheme();
  const { id, pipelineTab, library } = useParams();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();

  const [pipelineYaml, setPipelineYaml] = useState("");
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [pipelineName, setPipelineName] = useState("");
  const [pipelineDescription, setPipelineDescription] = useState("");
  const [saveError, setSaveError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [hasExperiment, setHasExperiment] = useState(false);

  const [runError, setRunError] = useState<string | null>(null);
  const [runResult, setRunResult] = useState<{
    message: string;
    severity: "success" | "error";
  } | null>(null);
  const [isRunning, setIsRunning] = useState(false);

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

  const {
    data: pipelineDetail,
    isLoading,
    isError,
  } = usePipelineDetail(library ?? "", id ?? "");

  const initialYaml = useMemo(
    () => pipelineDetail?.content ?? "",
    [pipelineDetail],
  );

  const initialExperiment = useMemo(() => {
    if (!initialYaml) return "";
    try {
      const doc = parse(initialYaml);
      const baseDir = doc?.spec?.output?.base_dir as string;
      if (baseDir) {
        const match = baseDir.match(/labs\/(.+)$/);
        return match?.[1] ?? "";
      }
    } catch {
      /* ignore */
    }
    return "";
  }, [initialYaml]);

  useEffect(() => {
    if (!validateMutation.isSuccess) return;
    const timer = setTimeout(() => validateMutation.reset(), 5000);
    return () => clearTimeout(timer);
  }, [validateMutation.isSuccess]);

  useEffect(() => {
    if (!runError) return;
    const timer = setTimeout(() => setRunError(null), 5000);
    return () => clearTimeout(timer);
  }, [runError]);

  const handleExperimentChange = useCallback((name: string) => {
    setHasExperiment(!!name);
  }, []);

  const handleValidate = useCallback(() => {
    if (!library || !pipelineYaml) return;
    validateMutation.mutate({ library, content: pipelineYaml });
  }, [library, pipelineYaml, validateMutation]);

  const handleRun = useCallback(async () => {
    if (!library || !id) return;
    setRunError(null);
    setRunResult(null);
    setIsRunning(true);

    try {
      const yamlToRun = pipelineYaml || initialYaml;
      if (!yamlToRun) {
        setRunError("No pipeline YAML available to run.");
        setIsRunning(false);
        return;
      }
      const { job_id } = await runPipeline(library, {
        pipeline_yaml: yamlToRun,
        timeout: 43200,
      });

      const POLL_INTERVAL = 1500;
      let result = await pollJob(job_id);
      while (result.status === "pending" || result.status === "running") {
        await new Promise((r) => setTimeout(r, POLL_INTERVAL));
        result = await pollJob(job_id);
      }

      queryClient.invalidateQueries({ queryKey: ["experiments"] });
      queryClient.invalidateQueries({ queryKey: ["experiment"] });

      if (result.status === "completed") {
        setRunResult({
          message: result.stdout || "Pipeline run completed successfully.",
          severity: "success",
        });
      } else {
        setRunResult({
          message: result.stderr || result.error || "Pipeline run failed.",
          severity: "error",
        });
      }
    } catch (err) {
      setRunError(
        err instanceof Error ? err.message : "An unexpected error occurred.",
      );
    } finally {
      setIsRunning(false);
    }
  }, [library, id, pipelineYaml, initialYaml, queryClient]);

  const handleOpenSaveDialog = useCallback(() => {
    setSaveError("");
    setPipelineName(id ?? "");
    try {
      const doc = parse(pipelineYaml || initialYaml);
      setPipelineDescription(doc?.metadata?.description ?? "");
    } catch {
      setPipelineDescription("");
    }
    setSaveDialogOpen(true);
  }, [id, pipelineYaml, initialYaml]);

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

    const yamlToSave = pipelineYaml || initialYaml;
    if (!yamlToSave) {
      setSaveError("No pipeline content to save.");
      return;
    }

    let finalYaml = yamlToSave;
    try {
      const doc = parse(finalYaml);
      if (doc?.metadata) {
        doc.metadata.name = trimmedName;
        const desc = pipelineDescription.trim();
        if (desc) {
          doc.metadata.description = desc;
        } else {
          delete doc.metadata.description;
        }
      }
      finalYaml = stringify(doc, { lineWidth: 0 });
    } catch {
      /* keep original */
    }

    setIsSaving(true);
    try {
      await updatePipeline(library ?? "", id ?? "", {
        name: trimmedName,
        content: finalYaml,
      });
      queryClient.invalidateQueries({ queryKey: ["pipelines", library] });
      queryClient.invalidateQueries({
        queryKey: ["pipeline", library, trimmedName],
      });
      handleCloseSaveDialog();

      if (trimmedName !== id) {
        navigate(`/${library}/pipelines/${encodeURIComponent(trimmedName)}`, {
          replace: true,
        });
      }
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
    initialYaml,
    library,
    id,
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

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100%",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  if (isError) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          height: "100%",
          gap: 2,
        }}
      >
        <Typography variant="h6">Pipeline not found</Typography>
        <Typography variant="body2" sx={{ color: "text.secondary" }}>
          The pipeline &quot;{id}&quot; does not exist or has been deleted.
        </Typography>
        <Button
          variant="primary"
          onClick={() => navigate(`/${library}/pipelines`)}
        >
          Back to Pipelines
        </Button>
      </Box>
    );
  }

  return (
    <PageWithTitle
      sx={{ overflow: "hidden" }}
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
            {toTitleCase(id ?? "")}
          </Typography>
          <Stack direction="row" sx={{ gap: "8px" }}>
            <Button
              variant="primary"
              onClick={handleOpenSaveDialog}
              disabled={!pipelineYaml && !initialYaml}
            >
              Save
            </Button>
            <Button
              variant="primary"
              onClick={handleValidate}
              disabled={
                (!pipelineYaml && !initialYaml) ||
                !hasExperiment ||
                validateMutation.isPending
              }
            >
              {validateMutation.isPending ? "Validating..." : "Validate"}
            </Button>
            <Button variant="primary" onClick={handleRun} disabled={isRunning}>
              {isRunning ? "Running..." : "Run"}
            </Button>
          </Stack>
        </Stack>
      }
    >
      <Stack
        direction="column"
        sx={{ width: "100%", height: "100%", minHeight: 0, overflow: "hidden" }}
      >
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
        {runError && (
          <Alert
            severity="error"
            onClose={() => setRunError(null)}
            sx={alertSx}
          >
            {runError}
          </Alert>
        )}
        {runResult && (
          <Alert
            severity={runResult.severity}
            onClose={() => setRunResult(null)}
            sx={alertSx}
          >
            {runResult.message}
          </Alert>
        )}

        <Box sx={{ borderBottom: 1, borderColor: "divider" }}>
          <Tabs value={selectedTab} onChange={handleTabChange}>
            <Tab label="Graph" id="pipeline-tab-0" />
            <Tab label="Yaml" id="pipeline-tab-1" />
          </Tabs>
        </Box>

        <TabPanel
          value={selectedTab}
          index={0}
          sx={{ flex: 1, minHeight: 0, overflow: "hidden" }}
        >
          <Box
            sx={{
              height: "100%",
              minHeight: 0,
              overflow: "hidden",
              paddingTop: "8px",
            }}
          >
            <PipelineBuilder
              initialYaml={initialYaml}
              experimentName={initialExperiment}
              onYamlChange={setPipelineYaml}
              onExperimentChange={handleExperimentChange}
            />
          </Box>
        </TabPanel>

        <TabPanel value={selectedTab} index={1} sx={{ flex: 1 }}>
          <Stack direction="column" sx={{ gap: "24px", paddingTop: "8px" }}>
            {!pipelineYaml && !initialYaml && (
              <Typography
                variant="body2"
                sx={{ color: "text.secondary", py: 2 }}
              >
                No pipeline YAML available.
              </Typography>
            )}
            {(pipelineYaml || initialYaml) && (
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
                <CodeBlock code={pipelineYaml || initialYaml} language="yaml" />
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
          <TextField
            autoFocus
            margin="dense"
            label="Pipeline Name"
            placeholder="Enter a name for the pipeline"
            variant="standard"
            autoComplete="off"
            fullWidth
            value={pipelineName}
            onChange={(e) => {
              setPipelineName(e.target.value);
              setSaveError("");
            }}
          />
          <TextField
            margin="dense"
            label="Description"
            placeholder="Optional description for the pipeline"
            variant="standard"
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
          <Button variant="primary" onClick={handleSave} disabled={isSaving}>
            {isSaving ? "Saving..." : "Save"}
          </Button>
        </DialogActions>
      </Dialog>
    </PageWithTitle>
  );
};

export default Pipeline;
