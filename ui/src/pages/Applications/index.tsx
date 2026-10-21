//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { useState } from "react";
import { PageWithTitle, MasTable } from "@/components";
import { Box, Stack, Typography, useTheme } from "@mui/material";
import { Button } from "@open-ui-kit/core";
import { useNavigate } from "react-router";

import { CreateItemDialog } from "@/components/CreateItemDialog";
import { scopeHref, useScopeId } from "@/api/scope";

const Applications = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const scopeId = useScopeId();
  const [createOpen, setCreateOpen] = useState(false);

  return (
    <Box>
      <PageWithTitle
        title={
          <Stack
            direction="row"
            sx={{ gap: "8px", justifyContent: "space-between" }}
          >
            <Typography
              variant={"h5"}
              sx={{
                color: theme.palette.vars.interactivePrimaryDefaultDefault,
              }}
            >
              Applications
            </Typography>
            <Button onClick={() => setCreateOpen(true)}>Add Application</Button>
          </Stack>
        }
      >
        <Stack direction={"column"} sx={{ gap: "24px" }}>
          <MasTable />
        </Stack>
      </PageWithTitle>
      <CreateItemDialog
        open={createOpen}
        kind="application"
        scopeId={scopeId}
        onClose={() => setCreateOpen(false)}
        onCreated={(sid, id) => navigate(scopeHref(sid, `/applications/${encodeURIComponent(id)}`))}
      />
    </Box>
  );
};

export default Applications;
