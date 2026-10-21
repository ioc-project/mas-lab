//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { PageWithTitle } from "@/components";
import {
  MRT_RowSelectionState,
  MRT_SortingState,
} from "material-react-table";
import {
  Table,
  EmptyState,
  TableProps,
  Tooltip,
} from "@open-ui-kit/core";
import { Delete as DeleteIcon } from "@mui/icons-material";
import {
  Button,
  Box,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  ListItemIcon,
  MenuItem,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import { useNavigate } from "react-router";
import { useScopeId, scopeHref } from "@/api/scope";
import { useCallback, useMemo, useState } from "react";
import { useDatasetsList, deleteDataset } from "@/api/apiCalls";
import type { DatasetSummary } from "@/api/apiCalls";
import { useQueryClient } from "@tanstack/react-query";
import { CreateItemDialog } from "@/components/CreateItemDialog";

const TABLE_BG = "transparent";

type DatasetColumnDefs = TableProps<DatasetSummary>["columns"];

const Datasets = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const library = useScopeId();
  const queryClient = useQueryClient();

  const { data: datasets, isLoading } = useDatasetsList(library);

  const [createOpen, setCreateOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [pendingDeleteNames, setPendingDeleteNames] = useState<string[]>([]);
  const [rowSelection, setRowSelection] = useState<MRT_RowSelectionState>({});
  const [sorting, setSorting] = useState<MRT_SortingState>([
    { id: "name", desc: false },
  ]);

  const rows = useMemo<DatasetSummary[]>(() => datasets ?? [], [datasets]);

  const handleRequestDelete = useCallback((names: string[]) => {
    setPendingDeleteNames(names);
    setDeleteDialogOpen(true);
  }, []);

  const handleConfirmDelete = useCallback(async () => {
    await Promise.all(
      pendingDeleteNames.map((name) => deleteDataset(library, name)),
    );
    queryClient.invalidateQueries({ queryKey: ["datasets-list", library] });
    setDeleteDialogOpen(false);
    setPendingDeleteNames([]);
    setRowSelection({});
  }, [pendingDeleteNames, library, queryClient]);

  const handleCancelDelete = useCallback(() => {
    setDeleteDialogOpen(false);
    setPendingDeleteNames([]);
  }, []);

  const columns = useMemo<DatasetColumnDefs>(
    () => [
      {
        accessorKey: "name",
        header: "Name",
        size: 300,
        accessorFn: (row) => (
          <Tooltip title={row.name} placement="top">
            <Typography
              variant="body2"
              sx={{
                cursor: "pointer",
                "&:hover": { textDecoration: "underline" },
                textOverflow: "ellipsis",
                overflow: "hidden",
                whiteSpace: "nowrap",
              }}
              onClick={() =>
                navigate(scopeHref(library, `/datasets/${encodeURIComponent(row.name)}`))
              }
            >
              {row.name}
            </Typography>
          </Tooltip>
        ),
      },
      {
        accessorKey: "description",
        header: "Description",
        size: 400,
      },
    ],
    [library, navigate],
  );

  const tableProps: TableProps<DatasetSummary> = {
    data: rows,
    columns,
    isLoading,
    rowCount: rows.length,
    title: { label: "" },
    topToolbarProps: {
      export: { enableExport: false },
    },
    enableRowActions: true,
    enableSorting: true,
    enableColumnResizing: true,
    renderRowActionMenuItems: ({ row, closeMenu }) => [
      <MenuItem
        key="delete"
        onClick={() => {
          handleRequestDelete([row.original.name]);
          closeMenu();
        }}
      >
        <ListItemIcon>
          <DeleteIcon />
        </ListItemIcon>
        Delete
      </MenuItem>,
    ],
    renderToolbarInternalActions: ({ table }) => {
      const selectedRows = table.getSelectedRowModel().rows;
      const hasSelection = selectedRows.length > 0;
      return (
        <Button
          variant="primary"
          color="negative"
          disabled={!hasSelection}
          onClick={() => {
            const names = selectedRows.map((r) => r.original.name);
            if (names.length > 0) handleRequestDelete(names);
          }}
        >
          Delete Selected ({selectedRows.length})
        </Button>
      );
    },
    renderEmptyRowsFallback: () => (
      <EmptyState
        title="No Datasets"
        description="No datasets found in this library."
      />
    ),
    muiTableBodyRowProps: () => ({
      sx: {
        cursor: "pointer",
        backgroundColor: TABLE_BG,
        "& > td": {
          backgroundColor: `${TABLE_BG} !important`,
        },
      },
    }),
    muiTablePaperProps: {
      sx: { backgroundColor: TABLE_BG },
    },
    state: { sorting, rowSelection },
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    enableRowSelection: true,
  };

  return (
    <Box>
      <PageWithTitle
        title={
          <Stack
            direction="row"
            sx={{ gap: "8px", justifyContent: "space-between", width: "100%" }}
          >
            <Typography
              variant="h5"
              sx={{ color: theme.palette.vars.interactivePrimaryDefaultDefault }}
            >
              Datasets
            </Typography>
            <Button onClick={() => setCreateOpen(true)}>Add Dataset</Button>
          </Stack>
        }
      >
        <Stack direction="column" sx={{ gap: "24px" }}>
          <Table {...tableProps} />
        </Stack>
      </PageWithTitle>

      <Dialog open={deleteDialogOpen} onClose={handleCancelDelete}>
        <DialogTitle>
          Delete Dataset{pendingDeleteNames.length > 1 ? "s" : ""}
        </DialogTitle>
        <DialogContent>
          <DialogContentText>
            Are you sure you want to delete{" "}
            {pendingDeleteNames.length === 1
              ? `"${pendingDeleteNames[0]}"`
              : `${pendingDeleteNames.length} datasets`}
            ? This action cannot be undone.
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCancelDelete}>Cancel</Button>
          <Button
            variant="primary"
            color="negative"
            onClick={handleConfirmDelete}
          >
            Delete
          </Button>
        </DialogActions>
      </Dialog>
      <CreateItemDialog
        open={createOpen}
        kind="dataset"
        scopeId={library}
        onClose={() => setCreateOpen(false)}
        onCreated={(sid, id) =>
          navigate(scopeHref(sid, `/datasets/${encodeURIComponent(id)}`))
        }
      />
    </Box>
  );
};

export default Datasets;
