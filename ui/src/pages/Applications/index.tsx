//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { PageWithTitle, MasTable } from "@/components";
import { Box, Stack, Typography, useTheme } from "@mui/material";
import { Button } from "@open-ui-kit/core";
import { useNavigate, useParams } from "react-router";

const Applications = () => {
  const theme = useTheme();
  const navigate = useNavigate();
  const { library = "" } = useParams<{ library: string }>();

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
            <Button
              variant="gradient"
              onClick={() => navigate(`/${library}/playground`)}
            >
              Add Application
            </Button>
          </Stack>
        }
      >
        <Stack direction={"column"} sx={{ gap: "24px" }}>
          <MasTable />
        </Stack>
      </PageWithTitle>
    </Box>
  );
};

export default Applications;
