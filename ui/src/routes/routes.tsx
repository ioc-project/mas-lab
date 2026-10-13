//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
import { Route, Routes } from "react-router";

import { Applications as ApplicationsIcon } from "@/assets/icons";

import LayoutWithSideNav from "@/components/LayoutWithSideNav/LayoutWithSideNav";

import Four0Four from "@/pages/404/404";
import Applications from "@/pages/Applications";
import Application from "@/pages/Application";

import type { AppRoute } from "@/routes/types.ts";
import Playground from "@/pages/Playground";
import { Transport as PlaygroundIcon } from "@/assets/icons";
import {
  Flaky as ExperimentsIcon,
  AccountTree as PipelinesIcon,
  Storage as DatasetsIcon,
  Settings as ControlPanelIcon,
  Layers as OverlaysIcon,
} from "@mui/icons-material";
import Experiments from "@/pages/Experiments";
import Experiment from "@/pages/Experiment";
import CreateExperiment from "@/pages/CreateExperiment";
import Pipelines from "@/pages/Pipelines";
import CreatePipeline from "@/pages/CreatePipeline";
import PipelineDetail from "@/pages/Pipeline";
import Datasets from "@/pages/Datasets";
import Dataset from "@/pages/Dataset";
import ControlPanel from "@/pages/ControlPanel";
import Overlays from "@/pages/Overlays";
import Overlay from "@/pages/Overlay";
import { LibraryRedirect } from "./LibraryRedirect";

export const PATHS = {
  applications: "/:library/applications",
  application: "/:library/applications/:id",
  applicationTab: "/:library/applications/:id/:applicationTab",
  playground: "/:library/playground",
  playgroundTab: "/:library/playground/:playgroundTab",
  experiments: "/:library/experiments",
  createExperiment: "/:library/experiments/new",
  createExperimentTab: "/:library/experiments/new/:experimentTab",
  editExperiment: "/:library/experiments/:id/edit",
  editExperimentTab: "/:library/experiments/:id/edit/:experimentTab",
  experiment: "/:library/experiments/:id",
  pipelines: "/:library/pipelines",
  createPipeline: "/:library/pipelines/new",
  createPipelineTab: "/:library/pipelines/new/:pipelineTab",
  pipeline: "/:library/pipelines/:id",
  pipelineTab: "/:library/pipelines/:id/:pipelineTab",
  datasets: "/:library/datasets",
  createDataset: "/:library/datasets/_create",
  dataset: "/:library/datasets/*",
  controlPanel: "/:library/control-panel",
  overlays: "/:library/overlays",
  createOverlay: "/:library/overlays/new",
  createOverlayTab: "/:library/overlays/new/:overlayTab",
  overlay: "/:library/overlays/:name",
  overlayTab: "/:library/overlays/:name/:overlayTab",
};

export const routes: {
  authenticated: AppRoute[];
  unauthenticated: AppRoute[];
} = {
  authenticated: [],
  unauthenticated: [
    {
      name: "Applications",
      path: PATHS.applications,
      element: <Applications />,
      sideBarProps: {
        title: "Applications",
        icon: ApplicationsIcon,
      },
    },
    {
      name: "Application",
      path: PATHS.application,
      element: <Application />,
    },
    {
      name: "ApplicationTab",
      path: PATHS.applicationTab,
      element: <Application />,
    },
    {
      name: "Playground",
      path: PATHS.playground,
      element: <Playground />,
      sideBarProps: {
        title: "Playground",
        icon: PlaygroundIcon,
      },
    },
    {
      name: "PlaygroundTab",
      path: PATHS.playgroundTab,
      element: <Playground />,
    },
    {
      name: "Experiments",
      path: PATHS.experiments,
      element: <Experiments />,
      sideBarProps: {
        title: "Experiments",
        icon: ExperimentsIcon,
      },
    },
    {
      name: "CreateExperiment",
      path: PATHS.createExperiment,
      element: <CreateExperiment />,
    },
    {
      name: "CreateExperimentTab",
      path: PATHS.createExperimentTab,
      element: <CreateExperiment />,
    },
    {
      name: "EditExperiment",
      path: PATHS.editExperiment,
      element: <CreateExperiment />,
    },
    {
      name: "EditExperimentTab",
      path: PATHS.editExperimentTab,
      element: <CreateExperiment />,
    },
    {
      name: "Experiment",
      path: PATHS.experiment,
      element: <Experiment />,
    },
    {
      name: "Pipelines",
      path: PATHS.pipelines,
      element: <Pipelines />,
      sideBarProps: {
        title: "Pipelines",
        icon: PipelinesIcon,
      },
    },
    {
      name: "CreatePipeline",
      path: PATHS.createPipeline,
      element: <CreatePipeline />,
    },
    {
      name: "CreatePipelineTab",
      path: PATHS.createPipelineTab,
      element: <CreatePipeline />,
    },
    {
      name: "Pipeline",
      path: PATHS.pipeline,
      element: <PipelineDetail />,
    },
    {
      name: "PipelineTab",
      path: PATHS.pipelineTab,
      element: <PipelineDetail />,
    },
    {
      name: "Datasets",
      path: PATHS.datasets,
      element: <Datasets />,
      sideBarProps: {
        title: "Datasets",
        icon: DatasetsIcon,
      },
    },
    {
      name: "CreateDataset",
      path: PATHS.createDataset,
      element: <Dataset />,
    },
    {
      name: "Dataset",
      path: PATHS.dataset,
      element: <Dataset />,
    },
    {
      name: "Overlays",
      path: PATHS.overlays,
      element: <Overlays />,
      sideBarProps: {
        title: "Overlays",
        icon: OverlaysIcon,
      },
    },
    {
      name: "CreateOverlay",
      path: PATHS.createOverlay,
      element: <Overlay />,
    },
    {
      name: "CreateOverlayTab",
      path: PATHS.createOverlayTab,
      element: <Overlay />,
    },
    {
      name: "Overlay",
      path: PATHS.overlay,
      element: <Overlay />,
    },
    {
      name: "OverlayTab",
      path: PATHS.overlayTab,
      element: <Overlay />,
    },
    {
      name: "ControlPanel",
      path: PATHS.controlPanel,
      element: <ControlPanel />,
      sideBarProps: {
        title: "Control Panel",
        icon: ControlPanelIcon,
      },
    },
  ],
};

export const unauthenticatedRoutes = routes.unauthenticated;
export const authenticatedRoutes = routes.authenticated;
export const allRoutes = [...routes.unauthenticated, ...routes.authenticated];

const renderRoute = (route: AppRoute) => (
  <Route key={route.name} path={route.path} element={route.element}>
    {route.children?.map(renderRoute)}
  </Route>
);

const AppRoutes = () => (
  <Routes>
    <Route path="/" element={<LayoutWithSideNav />}>
      <Route index element={<LibraryRedirect />} />
      {[...unauthenticatedRoutes, ...authenticatedRoutes].map(renderRoute)}
    </Route>
    <Route path="*" element={<Four0Four />} />
  </Routes>
);

export default AppRoutes;
