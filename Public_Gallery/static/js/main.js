/* =========================================================
   APNA GALLERY — GLOBAL JAVASCRIPT
   Phase 1
========================================================= */

"use strict";


/* =========================================================
   DOM READY
========================================================= */

document.addEventListener("DOMContentLoaded", () => {

    initPageLoader();

    initMobileMenu();

    initFlashMessages();

    initBackToTop();

    initSmoothLinks();

    initImageEffects();

});


/* =========================================================
   PAGE LOADER
========================================================= */

function initPageLoader() {

    const loader = document.getElementById("pageLoader");

    if (!loader) {
        return;
    }

    const hideLoader = () => {

        setTimeout(() => {
            loader.classList.add("loaded");
        }, 250);

    };

    if (document.readyState === "complete") {

        hideLoader();

    } else {

        window.addEventListener(
            "load",
            hideLoader,
            { once: true }
        );

    }

    /*
     * Safety fallback.
     * If a resource takes too long, don't keep
     * the user stuck on the loading screen.
     */

    setTimeout(() => {

        loader.classList.add("loaded");

    }, 3000);

}


/* =========================================================
   MOBILE NAVIGATION
========================================================= */

function initMobileMenu() {

    const menuButton =
        document.getElementById("mobileMenuBtn");

    const menu =
        document.getElementById("navMenu");

    if (!menuButton || !menu) {
        return;
    }

    menuButton.addEventListener("click", () => {

        const isOpen =
            menu.classList.toggle("open");

        menuButton.setAttribute(
            "aria-expanded",
            String(isOpen)
        );

    });


    /*
     * Close menu after clicking a link.
     */

    const links =
        menu.querySelectorAll("a");

    links.forEach((link) => {

        link.addEventListener("click", () => {

            menu.classList.remove("open");

            menuButton.setAttribute(
                "aria-expanded",
                "false"
            );

        });

    });


    /*
     * Close menu when clicking outside.
     */

    document.addEventListener("click", (event) => {

        const clickedInsideMenu =
            menu.contains(event.target);

        const clickedButton =
            menuButton.contains(event.target);

        if (
            !clickedInsideMenu &&
            !clickedButton
        ) {

            menu.classList.remove("open");

            menuButton.setAttribute(
                "aria-expanded",
                "false"
            );

        }

    });


    /*
     * Close menu with Escape.
     */

    document.addEventListener("keydown", (event) => {

        if (event.key === "Escape") {

            menu.classList.remove("open");

            menuButton.setAttribute(
                "aria-expanded",
                "false"
            );

        }

    });

}


/* =========================================================
   FLASH MESSAGES
========================================================= */

function initFlashMessages() {

    const container =
        document.getElementById("flashContainer");

    if (!container) {
        return;
    }

    const messages =
        container.querySelectorAll(".flash-message");

    messages.forEach((message) => {

        /*
         * Automatically remove flash messages
         * after 5 seconds.
         */

        setTimeout(() => {

            if (!message.isConnected) {
                return;
            }

            message.style.opacity = "0";
            message.style.transform =
                "translateX(20px)";

            setTimeout(() => {

                if (message.isConnected) {
                    message.remove();
                }

            }, 300);

        }, 5000);

    });

}


/* =========================================================
   BACK TO TOP
========================================================= */

function initBackToTop() {

    const button =
        document.getElementById("backToTop");

    if (!button) {
        return;
    }


    const updateButton = () => {

        if (window.scrollY > 450) {

            button.classList.add("visible");

        } else {

            button.classList.remove("visible");

        }

    };


    window.addEventListener(
        "scroll",
        updateButton,
        { passive: true }
    );


    button.addEventListener("click", () => {

        window.scrollTo({
            top: 0,
            behavior: "smooth"
        });

    });


    updateButton();

}


/* =========================================================
   SMOOTH INTERNAL LINKS
========================================================= */

function initSmoothLinks() {

    const links =
        document.querySelectorAll(
            'a[href^="#"]'
        );

    links.forEach((link) => {

        link.addEventListener("click", (event) => {

            const targetId =
                link.getAttribute("href");

            if (
                !targetId ||
                targetId === "#"
            ) {
                return;
            }

            const target =
                document.querySelector(targetId);

            if (!target) {
                return;
            }

            event.preventDefault();

            target.scrollIntoView({
                behavior: "smooth",
                block: "start"
            });

        });

    });

}


/* =========================================================
   IMAGE EFFECTS
========================================================= */

function initImageEffects() {

    const images =
        document.querySelectorAll("img");

    images.forEach((image) => {

        /*
         * Don't interfere with images that already
         * have their own error handlers.
         */

        image.addEventListener(
            "error",
            () => {

                image.classList.add(
                    "image-load-error"
                );

            },
            { once: true }
        );

    });

}


/* =========================================================
   BUTTON LOADING STATE
========================================================= */

function setButtonLoading(
    button,
    loading = true
) {

    if (!button) {
        return;
    }

    if (loading) {

        if (!button.dataset.originalText) {

            button.dataset.originalText =
                button.innerHTML;

        }

        button.disabled = true;

        button.innerHTML = `
            <span
                class="button-spinner"
                aria-hidden="true"
            ></span>
            <span>Loading...</span>
        `;

    } else {

        button.disabled = false;

        if (button.dataset.originalText) {

            button.innerHTML =
                button.dataset.originalText;

        }

    }

}


/* =========================================================
   SAFE FORM SUBMISSION
========================================================= */

function initFormLoading() {

    const forms =
        document.querySelectorAll(
            "form[data-loading]"
        );

    forms.forEach((form) => {

        form.addEventListener(
            "submit",
            () => {

                const submitButton =
                    form.querySelector(
                        'button[type="submit"], input[type="submit"]'
                    );

                if (!submitButton) {
                    return;
                }

                /*
                 * Don't prevent form submission.
                 * We only change the visual state.
                 */

                if (
                    submitButton.tagName ===
                    "BUTTON"
                ) {

                    setButtonLoading(
                        submitButton,
                        true
                    );

                } else {

                    submitButton.disabled = true;

                }

            }
        );

    });

}


/* =========================================================
   COPY TO CLIPBOARD
========================================================= */

async function copyText(text) {

    if (!text) {
        return false;
    }

    try {

        await navigator.clipboard.writeText(text);

        showToast("Copied successfully!");

        return true;

    } catch (error) {

        /*
         * Fallback for older browsers.
         */

        try {

            const textarea =
                document.createElement("textarea");

            textarea.value = text;

            textarea.style.position =
                "fixed";

            textarea.style.opacity = "0";

            document.body.appendChild(
                textarea
            );

            textarea.select();

            document.execCommand("copy");

            textarea.remove();

            showToast("Copied successfully!");

            return true;

        } catch (fallbackError) {

            console.error(
                "Copy failed:",
                fallbackError
            );

            return false;

        }

    }

}


/* =========================================================
   TOAST NOTIFICATION
========================================================= */

function showToast(message) {

    if (!message) {
        return;
    }

    let toast =
        document.getElementById(
            "globalToast"
        );


    /*
     * Create toast if it doesn't exist.
     */

    if (!toast) {

        toast =
            document.createElement("div");

        toast.id =
            "globalToast";

        toast.setAttribute(
            "role",
            "status"
        );

        toast.style.position =
            "fixed";

        toast.style.left =
            "50%";

        toast.style.bottom =
            "28px";

        toast.style.zIndex =
            "99999";

        toast.style.transform =
            "translate(-50%, 20px)";

        toast.style.padding =
            "11px 18px";

        toast.style.border =
            "1px solid rgba(255,255,255,.1)";

        toast.style.borderRadius =
            "12px";

        toast.style.background =
            "rgba(20,21,27,.96)";

        toast.style.color =
            "#fff";

        toast.style.fontSize =
            "13px";

        toast.style.fontWeight =
            "600";

        toast.style.boxShadow =
            "0 15px 40px rgba(0,0,0,.35)";

        toast.style.opacity =
            "0";

        toast.style.pointerEvents =
            "none";

        toast.style.transition =
            "opacity .25s ease, transform .25s ease";

        document.body.appendChild(
            toast
        );

    }


    toast.textContent =
        message;


    requestAnimationFrame(() => {

        toast.style.opacity =
            "1";

        toast.style.transform =
            "translate(-50%, 0)";

    });


    clearTimeout(
        toast._hideTimer
    );


    toast._hideTimer =
        setTimeout(() => {

            toast.style.opacity =
                "0";

            toast.style.transform =
                "translate(-50%, 20px)";

        }, 2500);

}


/* =========================================================
   CONFIRMATION HELPER
========================================================= */

function confirmAction(message) {

    if (!message) {

        message =
            "Are you sure you want to continue?";

    }

    return window.confirm(message);

}


/* =========================================================
   PASSWORD VISIBILITY
========================================================= */

function initPasswordToggle() {

    const buttons =
        document.querySelectorAll(
            "[data-password-toggle]"
        );

    buttons.forEach((button) => {

        button.addEventListener(
            "click",
            () => {

                const targetId =
                    button.getAttribute(
                        "data-password-toggle"
                    );

                if (!targetId) {
                    return;
                }

                const input =
                    document.getElementById(
                        targetId
                    );

                if (!input) {
                    return;
                }

                if (
                    input.type ===
                    "password"
                ) {

                    input.type =
                        "text";

                    button.setAttribute(
                        "aria-label",
                        "Hide password"
                    );

                } else {

                    input.type =
                        "password";

                    button.setAttribute(
                        "aria-label",
                        "Show password"
                    );

                }

            }
        );

    });

}


/* =========================================================
   CHARACTER COUNTER
========================================================= */

function initCharacterCounters() {

    const fields =
        document.querySelectorAll(
            "[data-max-length]"
        );

    fields.forEach((field) => {

        const maxLength =
            Number(
                field.dataset.maxLength
            );

        if (
            !Number.isFinite(maxLength) ||
            maxLength <= 0
        ) {
            return;
        }

        const counter =
            document.createElement("small");

        counter.className =
            "character-counter";

        counter.style.display =
            "block";

        counter.style.marginTop =
            "5px";

        counter.style.color =
            "#676a75";

        counter.style.fontSize =
            "11px";


        const updateCounter = () => {

            const length =
                field.value.length;

            counter.textContent =
                `${length}/${maxLength}`;

        };


        field.setAttribute(
            "maxlength",
            String(maxLength)
        );

        field.parentNode.appendChild(
            counter
        );

        field.addEventListener(
            "input",
            updateCounter
        );

        updateCounter();

    });

}


/* =========================================================
   NETWORK STATUS
========================================================= */

function initNetworkStatus() {

    const updateStatus = () => {

        document.body.dataset.online =
            navigator.onLine
                ? "true"
                : "false";

    };

    window.addEventListener(
        "online",
        updateStatus
    );

    window.addEventListener(
        "offline",
        updateStatus
    );

    updateStatus();

}


/* =========================================================
   GLOBAL ERROR PROTECTION
========================================================= */

window.addEventListener(
    "error",
    (event) => {

        /*
         * Log errors for debugging without
         * exposing sensitive information to users.
         */

        console.error(
            "Apna Gallery error:",
            event.message
        );

    }
);


/* =========================================================
   PUBLIC HELPERS
========================================================= */

window.ApnaGallery = {

    showToast,

    copyText,

    confirmAction,

    setButtonLoading

};


/* =========================================================
   OPTIONAL FEATURES
========================================================= */

initFormLoading();

initPasswordToggle();

initCharacterCounters();

initNetworkStatus();
