(() => {
  const screens = [...document.querySelectorAll(".screen")];
  const toast = document.getElementById("toast");
  let toastTimer;
  let bookingStep = 1;
  let bookingComplete = false;
  let selectedService = "";
  let enteredPhone = "";
  let selectedCountryCode = "+1";
  let selectedCustomerId = "jamie";
  let otpTimer = 30;
  let otpInterval;
  let otpMode = "";

  function notify(message) {
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 3200);
  }

  function go(name) {
    const target = document.getElementById(`screen-${name}`);
    if (!target) return;
    screens.forEach(screen => screen.classList.toggle("active", screen === target));
    document.querySelectorAll(".nav-link").forEach(link => link.classList.toggle("active", link.dataset.go === name || (name === "dashboard" && link.dataset.go === "dashboard")));
    document.getElementById("mobile-menu").hidden = true;
    document.getElementById("menu-toggle").setAttribute("aria-expanded", "false");
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (name === "booking") renderBooking();
    if (name === "customers" && customerSearch) {
      customerSearch.value = demoCustomers[selectedCustomerId].phone;
      filterCustomers(customerSearch.value);
    }
  }

  document.addEventListener("click", event => {
    const route = event.target.closest("[data-go]");
    if (route) {
      event.preventDefault();
      go(route.dataset.go);
    }
    const openTicket = event.target.closest("[data-open-ticket]");
    if (openTicket) {
      if (openTicket.closest(".customer-history")) syncTicketToSelectedCustomer();
      if (openTicket.closest("#screen-dashboard")) {
        const row = openTicket.closest("[data-customer]");
        chooseCustomer(row ? row.dataset.customer : "jamie");
        syncTicketToSelectedCustomer();
      }
      go("ticket");
    }
  });

  const themeToggle = document.getElementById("theme-toggle");
  const savedTheme = localStorage.getItem("harbour-theme");
  if (savedTheme === "dark") document.documentElement.dataset.theme = "dark";
  function updateThemeButton() {
    const isDark = document.documentElement.dataset.theme === "dark";
    themeToggle.setAttribute("aria-label", isDark ? "Switch to light mode" : "Switch to dark mode");
    themeToggle.title = isDark ? "Switch to light mode" : "Switch to dark mode";
  }
  updateThemeButton();
  themeToggle.addEventListener("click", () => {
    const isDark = document.documentElement.dataset.theme === "dark";
    if (isDark) delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = "dark";
    localStorage.setItem("harbour-theme", isDark ? "light" : "dark");
    updateThemeButton();
  });

  const menuToggle = document.getElementById("menu-toggle");
  menuToggle.addEventListener("click", () => {
    const menu = document.getElementById("mobile-menu");
    menu.hidden = !menu.hidden;
    menuToggle.setAttribute("aria-expanded", String(!menu.hidden));
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      document.getElementById("mobile-menu").hidden = true;
      menuToggle.setAttribute("aria-expanded", "false");
    }
  });

  const bookingContent = document.getElementById("booking-content");
  const nextButton = document.getElementById("booking-next");
  const backButton = document.getElementById("booking-back");
  const stepLabel = document.getElementById("booking-step-label");
  const progress = document.getElementById("progress-fill");
  const services = ["Check-up", "Cleaning", "Fillings", "Root canal", "Braces", "Implants", "Whitening", "Children’s dentistry"];
  const phoneMask = "+1 000-000-0001";
  let bookingPhone = phoneMask;
  const demoCustomers = {
    jamie: { name: "Jamie Demo", phone: "+1 000-000-0001", ticket: "DEMO-1042", request: "Check-up", source: "WhatsApp", status: "New" },
    riley: { name: "Riley Sample", phone: "+1 000-000-0002", ticket: "DEMO-1041", request: "Tooth concern", source: "Website", status: "Called" }
  };

  function chooseCustomer(id) {
    const customer = demoCustomers[id];
    if (!customer) return;
    selectedCustomerId = id;
    document.getElementById("customer-name").textContent = customer.name;
    document.getElementById("customer-summary").textContent = `${customer.phone} · First-time patient in demo`;
    document.getElementById("customer-history-detail").textContent = `Ticket ${customer.ticket} · ${customer.request} request received today.`;
    document.querySelectorAll(".customer-result").forEach(result => result.classList.toggle("selected", result.dataset.customer === id));
  }

  function syncTicketToSelectedCustomer() {
    const customer = demoCustomers[selectedCustomerId];
    document.getElementById("ticket-source").textContent = customer.source.toUpperCase();
    document.getElementById("ticket-number").textContent = customer.ticket;
    document.getElementById("ticket-request").textContent = `${customer.request} request · Received today at 9:41 AM`;
    const statusChip = document.getElementById("ticket-status-chip");
    statusChip.textContent = customer.status;
    statusChip.className = `badge ${customer.status.toLowerCase()}`;
    document.getElementById("ticket-status").value = customer.status;
    document.getElementById("ticket-call").href = `tel:${customer.phone.replace(/[^\d+]/g, "")}`;
    document.getElementById("ticket-call").textContent = `☎ Call ${customer.phone}`;
    document.getElementById("ticket-patient-name").textContent = customer.name;
    document.getElementById("ticket-patient-phone").textContent = customer.phone;
    document.getElementById("ticket-timestamp").textContent = `Today · 9:41 AM · ${customer.source}`;
    document.getElementById("ticket-request-detail").textContent = `Requested ${customer.request.toLowerCase()}. No clinical advice or treatment recorded.`;
  }

  function renderBooking(errorMessage = "") {
    if (bookingComplete) {
      stepLabel.textContent = "Complete";
      progress.style.width = "100%";
      backButton.hidden = true;
      nextButton.hidden = true;
      bookingContent.innerHTML = `<div class="success-panel"><span class="success-icon" aria-hidden="true">✓</span><p class="eyebrow">Request received</p><h1>Your request is received</h1><p>We will call you shortly to confirm the details.</p><div class="ticket-number">Ticket number: DEMO-1042</div><p><b>${selectedService || "Check-up"}</b> · Mobile ${bookingPhone}</p><button class="button whatsapp full" data-go="wa-chat">◉ Chat on WhatsApp</button><p class="small-note">This demo ticket does not reserve an appointment time.</p></div>`;
      return;
    }
    stepLabel.textContent = `Step ${bookingStep} of 4`;
    progress.style.width = `${bookingStep * 25}%`;
    backButton.hidden = bookingStep === 1;
    nextButton.hidden = false;
    nextButton.disabled = false;
    nextButton.classList.remove("loading-dots");
    nextButton.textContent = bookingStep === 2 ? "Send verification code" : bookingStep === 3 ? "Verify code" : "Continue";

    if (bookingStep === 1) {
      bookingContent.innerHTML = `<p class="eyebrow">Appointment request</p><h1>What would you like help with?</h1><p class="lead">Choose a service to get started. Not sure? Message us and we’ll help.</p><div class="choice-list">${services.map(service => `<button class="choice-card ${selectedService === service ? "selected" : ""}" data-service="${service}" aria-pressed="${selectedService === service}"><span class="radio" aria-hidden="true"></span><span>${service}</span></button>`).join("")}</div>${errorMessage ? `<p class="inline-error" role="alert">${errorMessage}</p>` : ""}`;
      nextButton.disabled = !selectedService;
      bookingContent.querySelectorAll("[data-service]").forEach(button => button.addEventListener("click", () => {
        selectedService = button.dataset.service;
        renderBooking();
      }));
    } else if (bookingStep === 2) {
      bookingContent.innerHTML = `<p class="eyebrow">Contact details</p><h1>What number can we reach you on?</h1><p class="lead">We’ll use this number to confirm your request.</p><div class="phone-input-row"><select class="field" id="country-code" aria-label="Country code">${["+1", "+44", "+91", "+61"].map(code => `<option value="${code}" ${selectedCountryCode === code ? "selected" : ""}>${code}</option>`).join("")}</select><input id="phone-number" inputmode="tel" autocomplete="tel-national" placeholder="Mobile number" aria-label="Mobile number" value="${enteredPhone}"></div><p class="small-note">Choose your country code. Your number is only used to respond to this request.</p>${errorMessage ? `<p class="inline-error" role="alert">${errorMessage}</p>` : ""}`;
      document.getElementById("country-code").addEventListener("change", event => { selectedCountryCode = event.target.value; });
      document.getElementById("phone-number").addEventListener("input", event => {
        enteredPhone = event.target.value;
        const valid = event.target.value.replace(/\D/g, "").length >= 7;
        nextButton.disabled = !valid;
        if (errorMessage) event.target.setAttribute("aria-invalid", "true");
      });
      nextButton.disabled = true;
    } else if (bookingStep === 3) {
      nextButton.disabled = true;
      const demoHint = otpMode ? "" : `<p class="small-note">Prototype code: <b>123456</b></p>`;
      const errorHtml = errorMessage ? `<p class="inline-error" role="alert">${errorMessage}</p>` : "";
      bookingContent.innerHTML = `<p class="eyebrow">Verify your number</p><h1>${otpMode === "expired" ? "This code has expired" : "Enter your verification code"}</h1><p class="lead">${otpMode === "expired" ? "Request a new code to continue." : `We sent a code to ${bookingPhone}.`}</p>${otpMode === "expired" ? `<button class="button secondary full" id="resend-code">Send a new code</button>` : `<div class="otp-row" role="group" aria-label="6-digit verification code">${Array.from({ length: 6 }, (_, index) => `<input class="otp-slot" inputmode="numeric" autocomplete="${index === 0 ? "one-time-code" : "off"}" maxlength="1" aria-label="Digit ${index + 1} of 6" ${errorMessage ? 'aria-invalid="true" aria-describedby="otp-error"' : ""}>`).join("")}</div><input id="otp-combined" class="visually-hidden" autocomplete="one-time-code" inputmode="numeric" aria-label="6-digit verification code"><p class="resend-line" id="otp-countdown" aria-live="polite"></p><button class="text-button" id="show-expired">Show expired-code state (demo)</button>${demoHint}`}${errorMessage ? `<p id="otp-error" class="inline-error" role="alert">${errorMessage}</p>` : ""}`;
      nextButton.hidden = otpMode === "expired";
      if (otpMode === "expired") {
        document.getElementById("resend-code").addEventListener("click", () => {
          const button = document.getElementById("resend-code");
          button.disabled = true;
          button.textContent = "Sending code…";
          setTimeout(() => {
            otpMode = "";
            startOtpTimer();
            renderBooking();
            notify("A new demo code is ready.");
          }, 500);
        });
      } else {
        wireOtp();
        startOtpTimer();
        document.getElementById("show-expired").addEventListener("click", () => {
          otpMode = "expired";
          clearInterval(otpInterval);
          renderBooking();
        });
      }
    } else {
      bookingContent.innerHTML = `<p class="eyebrow">Appointment type</p><h1>What kind of appointment is this?</h1><p class="lead">Choose the option that best describes your visit.</p><div class="choice-list"><button class="choice-card appointment-choice" aria-pressed="false"><span class="radio"></span><span>New appointment</span></button><button class="choice-card appointment-choice" aria-pressed="false"><span class="radio"></span><span>Follow-up</span></button></div>`;
      nextButton.disabled = true;
      bookingContent.querySelectorAll(".appointment-choice").forEach(button => button.addEventListener("click", () => {
        bookingContent.querySelectorAll(".appointment-choice").forEach(item => {
          item.classList.remove("selected");
          item.setAttribute("aria-pressed", "false");
        });
        button.classList.add("selected");
        button.setAttribute("aria-pressed", "true");
        nextButton.disabled = false;
      }));
    }
  }

  function wireOtp() {
    const inputs = [...bookingContent.querySelectorAll(".otp-slot")];
    const hiddenInput = document.getElementById("otp-combined");
    const updateOtp = () => {
      hiddenInput.value = inputs.map(input => input.value).join("");
      nextButton.disabled = hiddenInput.value.length !== 6;
    };
    inputs.forEach((input, index) => {
      input.addEventListener("input", () => {
        input.value = input.value.replace(/\D/g, "").slice(-1);
        if (input.value && inputs[index + 1]) inputs[index + 1].focus();
        updateOtp();
      });
      input.addEventListener("keydown", event => {
        if (event.key === "Backspace" && !input.value && inputs[index - 1]) inputs[index - 1].focus();
        if (event.key === "ArrowLeft" && inputs[index - 1]) inputs[index - 1].focus();
        if (event.key === "ArrowRight" && inputs[index + 1]) inputs[index + 1].focus();
      });
      input.addEventListener("paste", event => {
        const digits = event.clipboardData.getData("text").replace(/\D/g, "").slice(0, 6);
        if (!digits) return;
        event.preventDefault();
        [...digits].forEach((digit, digitIndex) => { if (inputs[digitIndex]) inputs[digitIndex].value = digit; });
        updateOtp();
        inputs[Math.min(digits.length, 5)].focus();
      });
    });
    hiddenInput.addEventListener("input", () => {
      const digits = hiddenInput.value.replace(/\D/g, "").slice(0, 6);
      [...digits].forEach((digit, index) => { if (inputs[index]) inputs[index].value = digit; });
      updateOtp();
    });
  }

  function startOtpTimer() {
    clearInterval(otpInterval);
    otpTimer = 30;
    const update = () => {
      const line = document.getElementById("otp-countdown");
      if (!line) { clearInterval(otpInterval); return; }
      if (otpTimer > 0) {
        line.textContent = `Resend code in 00:${String(otpTimer).padStart(2, "0")}`;
        otpTimer -= 1;
      } else {
        line.innerHTML = `Didn’t get a code? <button id="resend-code">Resend code</button>`;
        document.getElementById("resend-code").addEventListener("click", () => {
          startOtpTimer();
          notify("A new demo code is ready.");
        });
        clearInterval(otpInterval);
      }
    };
    update();
    otpInterval = setInterval(update, 1000);
  }

  nextButton.addEventListener("click", () => {
    if (bookingStep === 1) bookingStep = 2;
    else if (bookingStep === 2) {
      const number = document.getElementById("phone-number");
      if (!number || number.value.replace(/\D/g, "").length < 7) {
        renderBooking("Enter a valid mobile number to continue.");
        return;
      }
      const digits = number.value.replace(/\D/g, "");
      selectedCountryCode = document.getElementById("country-code").value;
      bookingPhone = `${selectedCountryCode} ${"•".repeat(Math.max(0, digits.length - 2))}${digits.slice(-2)}`;
      loadStep("Sending code…", () => {
        bookingStep = 3;
        otpMode = "";
        renderBooking();
      });
      return;
    } else if (bookingStep === 3) {
      const code = [...bookingContent.querySelectorAll(".otp-slot")].map(input => input.value).join("");
      if (code !== "123456") {
        renderBooking("That code doesn’t match. Check it and try again.");
        return;
      }
      loadStep("Verifying…", () => {
        bookingStep = 4;
        renderBooking();
      });
      return;
    } else {
      const appointmentType = bookingContent.querySelector(".appointment-choice.selected");
      if (!appointmentType) return;
      clearInterval(otpInterval);
      loadStep("Creating request…", () => {
        bookingComplete = true;
        renderBooking();
      });
      return;
    }
    renderBooking();
  });
  function loadStep(label, done) {
    nextButton.disabled = true;
    nextButton.classList.add("loading-dots");
    nextButton.setAttribute("aria-busy", "true");
    nextButton.textContent = label;
    setTimeout(() => {
      nextButton.classList.remove("loading-dots");
      nextButton.removeAttribute("aria-busy");
      done();
    }, 550);
  }
  backButton.addEventListener("click", () => {
    if (bookingStep > 1) bookingStep -= 1;
    otpMode = "";
    renderBooking();
  });

  const search = document.getElementById("global-search");
  const customerSearch = document.getElementById("customer-search");
  function filterCustomers(value) {
    const query = value.toLowerCase().replace(/[\W_]/g, "");
    let visibleCount = 0;
    document.querySelectorAll(".customer-result").forEach(result => {
      const normalizedResult = result.textContent.toLowerCase().replace(/[\W_]/g, "");
      result.hidden = query.length > 0 && !normalizedResult.includes(query);
      if (!result.hidden) visibleCount += 1;
    });
    document.getElementById("results-count").textContent = `${visibleCount} ${visibleCount === 1 ? "match" : "matches"}`;
    document.getElementById("no-customer-results").hidden = visibleCount > 0;
    if (visibleCount === 1) {
      const match = [...document.querySelectorAll(".customer-result")].find(result => !result.hidden);
      if (match) chooseCustomer(match.dataset.customer);
    }
  }
  search.addEventListener("keydown", event => {
    if (event.key === "Enter" && search.value.trim()) {
      go("customers");
      customerSearch.value = search.value.trim();
      filterCustomers(customerSearch.value);
    }
  });
  customerSearch.addEventListener("input", event => filterCustomers(event.target.value));
  document.querySelector(".clear-search").addEventListener("click", () => {
    customerSearch.value = "";
    filterCustomers("");
    customerSearch.focus();
  });
  filterCustomers(customerSearch.value);
  document.querySelectorAll(".customer-result").forEach(result => result.addEventListener("click", () => {
    chooseCustomer(result.dataset.customer);
    notify("Showing synthetic demo customer history.");
  }));
  document.querySelectorAll(".filter-chip").forEach(chip => chip.addEventListener("click", () => {
    document.querySelectorAll(".filter-chip").forEach(item => item.classList.remove("active"));
    chip.classList.add("active");
    notify(`${chip.textContent} view selected.`);
  }));
  document.getElementById("ticket-status").addEventListener("change", event => {
    const customer = demoCustomers[selectedCustomerId];
    customer.status = event.target.value;
    const chip = document.getElementById("ticket-status-chip");
    chip.textContent = customer.status;
    chip.className = `badge ${customer.status.toLowerCase()}`;
    notify(`Demo ticket status changed to ${event.target.value}.`);
  });
  document.getElementById("file-upload").addEventListener("change", event => {
    const list = document.getElementById("file-list");
    [...event.target.files].forEach(file => {
      const item = document.createElement("div");
      item.className = "file-item";
      if (file.type.startsWith("image/")) {
        const preview = document.createElement("img");
        preview.src = URL.createObjectURL(file);
        preview.alt = `Preview of ${file.name}`;
        item.appendChild(preview);
      } else {
        const icon = document.createElement("span");
        icon.className = "file-icon";
        icon.textContent = "PDF";
        item.appendChild(icon);
      }
      const details = document.createElement("span");
      details.textContent = `${file.name} · ${(file.size / 1024).toFixed(0)} KB · local demo`;
      item.appendChild(details);
      list.appendChild(item);
    });
    if (event.target.files.length) notify("File added to this local demo view only.");
  });
  document.getElementById("add-note").addEventListener("click", () => {
    const field = document.querySelector(".action-panel textarea");
    if (!field.value.trim()) { notify("Enter a note before saving."); field.focus(); return; }
    notify("Internal note saved in this demo view only.");
    field.value = "";
  });
  const medicineName = document.getElementById("medicine-name");
  const medicineDirections = document.getElementById("medicine-directions");
  const addMedicine = document.getElementById("add-medicine");
  function updateMedicineButton() {
    addMedicine.disabled = !medicineName.value.trim() || !medicineDirections.value.trim();
  }
  medicineName.addEventListener("input", updateMedicineButton);
  medicineDirections.addEventListener("input", updateMedicineButton);
  addMedicine.addEventListener("click", () => {
    notify("Demo only: medicine details were not saved to a patient record.");
    medicineName.value = "";
    medicineDirections.value = "";
    updateMedicineButton();
  });

  document.getElementById("patient-chat-form").addEventListener("submit", event => {
    event.preventDefault();
    const input = document.getElementById("patient-chat-input");
    if (!input.value.trim()) return;
    addBubble(document.getElementById("chat-messages"), input.value.trim(), "outgoing");
    input.value = "";
  });
  document.getElementById("staff-reply-form").addEventListener("submit", event => {
    event.preventDefault();
    const input = document.getElementById("staff-reply");
    if (!input.value.trim()) { notify("Write a reply before sending."); input.focus(); return; }
    if (event.currentTarget.querySelector("select").selectedIndex === 1) notify("Internal note saved in this demo only.");
    else {
      addBubble(document.querySelector(".staff-messages"), input.value.trim(), "outgoing");
      notify("Demo reply added to the conversation.");
    }
    input.value = "";
  });
  function addBubble(parent, message, kind) {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${kind}`;
    bubble.textContent = message;
    const time = document.createElement("time");
    time.textContent = "now · demo";
    bubble.appendChild(time);
    parent.appendChild(bubble);
    parent.scrollTop = parent.scrollHeight;
  }

  renderBooking();
})();
