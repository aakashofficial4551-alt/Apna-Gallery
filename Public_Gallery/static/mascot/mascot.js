document.addEventListener("DOMContentLoaded", function() {
    const mascot = document.getElementById('ff-mascot');
    const box = document.getElementById('mascot-box');

    // Har 15 second me animation loop chalega
    setInterval(() => {
        // 1. Box zameen se bahar aayega
        box.classList.add('box-up');

        // 2. Character skate karte hue aayegi (1 second baad)
        setTimeout(() => {
            mascot.style.transition = "all 3s ease-in-out";
            mascot.classList.add('skate-in');
            mascot.classList.remove('jump-on-box');
        }, 1000);

        // 3. Character box ke upar jump karegi (4.5 second baad)
        setTimeout(() => {
            mascot.style.transition = "all 0.8s cubic-bezier(0.25, 1, 0.5, 1)";
            mascot.classList.remove('skate-in');
            mascot.classList.add('jump-on-box');
        }, 4500);

        // 4. Character wapas neeche utregi aur chali jayegi (10 second baad)
        setTimeout(() => {
            mascot.style.transition = "all 2s ease-in-out";
            mascot.classList.remove('jump-on-box');
            mascot.style.transform = "translateX(120vw)"; /* Screen ke bahar nikal jayegi */
        }, 10000);

        // 5. Box wapas neeche chala jayega, aur position reset hogi (13 second baad)
        setTimeout(() => {
            box.classList.remove('box-up');
            setTimeout(() => {
                // Reset mascot to left side invisibly
                mascot.style.transition = "none";
                mascot.style.transform = ""; 
                mascot.classList.remove('skate-in', 'jump-on-box');
            }, 1000);
        }, 13000);

    }, 20000); // Repeat every 20 seconds
});
