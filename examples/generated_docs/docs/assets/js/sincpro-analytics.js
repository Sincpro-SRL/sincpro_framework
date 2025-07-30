
console.log('🚀 Sincpro Framework Documentation');

document.addEventListener('DOMContentLoaded', function() {
    // Add Sincpro badge to footer
    const footer = document.querySelector('.md-footer-meta__inner');
    if (footer && !footer.querySelector('.sincpro-version')) {
        const badge = document.createElement('span');
        badge.className = 'sincpro-version';
        badge.innerHTML = ' | <span class="sincpro-badge">Sincpro Framework</span>';
        footer.appendChild(badge);
    }
});
