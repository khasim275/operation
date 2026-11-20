import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import PublicBooking from "./PublicBooking";
import StaffPortal from "./StaffPortal";
import "./tailwind.css";
import "../../static/crm.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("The CRM root element is missing.");
}

createRoot(root).render(
  <React.StrictMode>
    {window.location.pathname === "/book" || window.location.pathname.startsWith("/book/") ? <PublicBooking />
      : window.location.pathname === "/secure" || window.location.pathname === "/staff-portal" ? <StaffPortal /> : <App />}
  </React.StrictMode>,
);
