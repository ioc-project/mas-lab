//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import {
  PageWithTitle,
  ExperimentsTable,
  BenchmarkOps,
  TabPanel,
} from "@/components";
import type { ExperimentJobStatus } from "@/components/ExperimentsTable/ExperimentsTable";
import { Alert, Box, Stack, Typography, useTheme } from "@mui/material";
import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { useNavigate } from "react-router";
import { scopeHref, toScopeId, useScopeId } from "@/api/scope";
import {
  useExperiments,
  type ExperimentSummary,
  deleteExperimentApi,
  pollJob,
  deleteExperimentCache,
  downloadBenchmarkExport,
} from "@/api/apiCalls";
import { fetchRuns, startRun } from "@/api/runs";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Tab, Tabs } from "@open-ui-kit/core";

const POLL_INTERVAL = 2000;

const experimentJobKey = (exp: Pick<ExperimentSummary, "name" | "library">) =>
  exp.library ? `${exp.library}/${exp.name}` : exp.name;

const Experiments = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const library = useScopeId();
  const queryClient = useQueryClient();

  const { data: experiments = [], isLoading } = useExperiments(library);

  const experimentByKey = useMemo(() => {
    const map = new Map<string, ExperimentSummary>();
    for (const exp of experiments) {
      map.set(experimentJobKey(exp), exp);
    }
    return map;
  }, [experiments]);

  const [runningJobs, setRunningJobs] = useState<
    Record<string, ExperimentJobStatus>
  >({});
  const [alertMessage, setAlertMessage] = useState<{
    message: string;
    severity: "success" | "error";
  } | null>(null);
  const [tab, setTab] = useState(0);

  const pollTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  const handleAdd = () => {
    navigate(scopeHref(library, "/experiments/new"));
  };

  const resolveExperiment = useCallback(
    (name: string) => {
      const direct = experimentByKey.get(experimentJobKey({ name, library }));
      if (direct) return direct;
      return experiments.find((e) => e.name === name);
    },
    [experimentByKey, experiments, library],
  );

  const handleEdit = useCallback(
    (name: string) => {
      const exp = resolveExperiment(name);
      const expLibrary = exp?.library ?? library;
      navigate(scopeHref(expLibrary, `/experiments/${encodeURIComponent(name)}/edit`));
    },
    [library, navigate, resolveExperiment],
  );

  useEffect(() => {
    if (!alertMessage) return;
    const timer = setTimeout(() => setAlertMessage(null), 5000);
    return () => clearTimeout(timer);
  }, [alertMessage]);

  const handleDelete = useCallback(
    async (names: string[]) => {
      try {
        await Promise.all(
          names.map((name) => {
            const exp = resolveExperiment(name);
            return deleteExperimentApi(exp?.library ?? library, name);
          }),
        );
        queryClient.resetQueries({ queryKey: ["experiments"] });
      } catch (err) {
        setAlertMessage({
          message:
            err instanceof Error
              ? err.message
              : "Failed to delete experiment(s).",
          severity: "error",
        });
        throw err;
      }
    },
    [library, queryClient, resolveExperiment],
  );

  const startPolling = useCallback((jobKey: string, jobId: string) => {
    const poll = async () => {
      try {
        const job = await pollJob(jobId);
        const isTerminal =
          job.status === "completed" ||
          job.status === "failed" ||
          job.status === "cancelled" ||
          job.status === "timeout";

        setRunningJobs((prev) => ({
          ...prev,
          [jobKey]: {
            jobId,
            status: job.status,
            progress: isTerminal ? 100 : (prev[jobKey]?.progress ?? 0),
            stdout: job.stdout || undefined,
            stderr: job.stderr || undefined,
          },
        }));

        if (isTerminal) {
          delete pollTimers.current[jobKey];
        } else {
          pollTimers.current[jobKey] = setTimeout(poll, POLL_INTERVAL);
        }
      } catch {
        delete pollTimers.current[jobKey];
        setRunningJobs((prev) => {
          const next = { ...prev };
          delete next[jobKey];
          return next;
        });
      }
    };

    pollTimers.current[jobKey] = setTimeout(poll, POLL_INTERVAL);
  }, []);

  useEffect(() => {
    const recoverJobs = async () => {
      try {
        const runs = (await fetchRuns()).filter((r) => r.kind === "experiment");
        const seen = new Set<string>();
        for (const run of runs) {
          const jobKey = experimentJobKey({
            name: run.item,
            library: run.scope || library,
          });
          if (seen.has(jobKey)) continue;
          seen.add(jobKey);
          const isTerminal =
            run.status === "completed" ||
            run.status === "failed" ||
            run.status === "cancelled" ||
            run.status === "timeout";
          if (isTerminal) {
            setRunningJobs((prev) => ({
              ...prev,
              [jobKey]: {
                jobId: run.id,
                status: run.status as ExperimentJobStatus["status"],
                progress: 100,
              },
            }));
          } else if (!pollTimers.current[jobKey]) {
            setRunningJobs((prev) => ({
              ...prev,
              [jobKey]: {
                jobId: run.id,
                status: run.status as ExperimentJobStatus["status"],
                progress: 0,
              },
            }));
            startPolling(jobKey, run.id);
          }
        }
      } catch {
        /* server may be unreachable on first load */
      }
    };

    recoverJobs();
    const timer = window.setInterval(() => {
      void recoverJobs();
    }, POLL_INTERVAL);
    return () => window.clearInterval(timer);
  }, [library, startPolling]);

  const handleRun = useCallback(
    async (name: string) => {
      const exp = resolveExperiment(name);
      const expLibrary = exp?.library ?? library;
      const jobKey = experimentJobKey({ name, library: expLibrary });

      setRunningJobs((prev) => ({
        ...prev,
        [jobKey]: { jobId: "", status: "pending", progress: 0 },
      }));

      try {
        const started = await startRun({
          scope: toScopeId(expLibrary),
          kind: "experiment",
          item: name,
        });
        const job_id = started.id;

        setRunningJobs((prev) => ({
          ...prev,
          [jobKey]: { jobId: job_id, status: "running", progress: 0 },
        }));

        startPolling(jobKey, job_id);
      } catch {
        setRunningJobs((prev) => {
          const next = { ...prev };
          delete next[jobKey];
          return next;
        });
      }
    },
    [library, resolveExperiment, startPolling],
  );

  const handleDeleteCache = useCallback(
    async (experimentName: string) => {
      try {
        await deleteExperimentCache(library, experimentName);
        queryClient.invalidateQueries({
          queryKey: ["experiment", experimentName],
        });
        setAlertMessage({
          message: `Cache deleted for "${experimentName}".`,
          severity: "success",
        });
      } catch (err) {
        setAlertMessage({
          message:
            err instanceof Error ? err.message : "Failed to delete cache.",
          severity: "error",
        });
      }
    },
    [library, queryClient],
  );

  const handleView = useCallback(
    (experimentName: string) => {
      const exp = resolveExperiment(experimentName);
      const expLibrary = exp?.library ?? library;
      navigate(
        scopeHref(expLibrary, `/experiments/${encodeURIComponent(experimentName)}`),
      );
    },
    [library, navigate, resolveExperiment],
  );

  const handleExport = useCallback(
    async (experimentName: string) => {
      try {
        await downloadBenchmarkExport(library, experimentName);
      } catch (err) {
        setAlertMessage({
          severity: "error",
          message: `Export failed: ${err instanceof Error ? err.message : String(err)}`,
        });
      }
    },
    [library],
  );

  return (
    <Box sx={{ position: "relative" }}>
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
      <PageWithTitle
        title={
          <Stack
            direction="row"
            sx={{ gap: "8px", justifyContent: "space-between" }}
          >
            <Typography
              variant="h5"
              sx={{
                color: theme.palette.vars.interactivePrimaryDefaultDefault,
              }}
            >
              Experiments
            </Typography>
            {tab === 0 && (
              <Button variant="gradient" onClick={handleAdd}>
                Add Experiment
              </Button>
            )}
          </Stack>
        }
      >
        <Box sx={{ borderBottom: 1, borderColor: "divider", mb: 2 }}>
          <Tabs value={tab} onChange={(_e, v) => setTab(v)}>
            <Tab label="Experiments" id="experiments-tab-0" />
            <Tab label="Benchmark Utilities" id="experiments-tab-1" />
          </Tabs>
        </Box>

        <TabPanel value={tab} index={0}>
          <ExperimentsTable
            data={experiments}
            isLoading={isLoading}
            onEdit={handleEdit}
            onRun={handleRun}
            onExport={handleExport}
            onDelete={handleDelete}
            onDeleteCache={handleDeleteCache}
            onView={handleView}
            runningJobs={runningJobs}
            defaultHiddenColumns={["description", "version", "path", "library"]}
            showLibraryColumn
          />
        </TabPanel>

        <TabPanel value={tab} index={1}>
          <BenchmarkOps library={library} />
        </TabPanel>
      </PageWithTitle>
    </Box>
  );
};

export default Experiments;
