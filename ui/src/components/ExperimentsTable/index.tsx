//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import type { ExperimentSummary } from "@/api/apiCalls";
import { Stack } from "@open-ui-kit/core";
import { ExperimentsTable, type ExperimentJobStatus } from "./ExperimentsTable";

interface ExperimentsTablePropsWrapper {
  data: ExperimentSummary[];
  isLoading: boolean;
  defaultHiddenColumns?: string[];
  title?: string;
  onEdit?: (name: string) => void;
  onRun?: (name: string) => void;
  onExport?: (name: string) => void;
  onDeleteCache?: (experimentName: string) => void;
  onView?: (experimentName: string) => void;
  onDelete?: (names: string[]) => void;
  runningJobs?: Record<string, ExperimentJobStatus>;
  showLibraryColumn?: boolean;
}

export const ExperimentsTableWrapper = ({
  data,
  isLoading,
  defaultHiddenColumns = [],
  showLibraryColumn,
  title,
  onEdit,
  onRun,
  onExport,
  onDeleteCache,
  onView,
  onDelete,
  runningJobs,
}: ExperimentsTablePropsWrapper) => {
  return (
    <Stack direction="column" sx={{ gap: "8px", width: "100%" }}>
      <ExperimentsTable
        data={data}
        isLoading={isLoading}
        onDelete={onDelete}
        onEdit={onEdit}
        onRun={onRun}
        onExport={onExport}
        onDeleteCache={onDeleteCache}
        onView={onView}
        runningJobs={runningJobs}
        defaultHiddenColumns={defaultHiddenColumns}
        showLibraryColumn={showLibraryColumn}
        title={title}
      />
    </Stack>
  );
};
