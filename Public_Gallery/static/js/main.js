// Focus Mode Toggle
function toggleFocus() {
    document.body.classList.toggle('focus-mode');
    let btn = document.getElementById('focus-btn');
    if (document.body.classList.contains('focus-mode')) {
        btn.innerText = "Exit Focus Mode";
        btn.style.borderColor = "#e50914";
        btn.style.color = "#e50914";
    } else {
        btn.innerText = "Focus Mode 👁️";
        btn.style.borderColor = "#555";
        btn.style.color = "white";
    }
}

// Mascot Click Event
function pokeMascot() {
    alert("Bloop! I am the Vault Guardian. Keep exploring!");
}
