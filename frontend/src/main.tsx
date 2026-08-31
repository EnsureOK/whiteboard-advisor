import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import ShareView from "./ShareView";
import BrokerPortal from "./BrokerPortal";
import Workbench from "./workbench/Workbench";
import "./index.css";

const params = new URLSearchParams(location.search);
const shareToken = params.get("share");
const portal = params.get("portal");
const view = params.get("view");

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {view === "workbench" ? <Workbench /> : portal ? <BrokerPortal /> : shareToken ? <ShareView token={shareToken} /> : <App />}
  </React.StrictMode>
);
