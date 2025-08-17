class CardSearch {
    constructor() {
        this.searchInput = document.getElementById('searchInput');
        this.resultsContainer = document.getElementById('results');
        this.loadingIndicator = document.getElementById('loading');
        this.errorMessage = document.getElementById('error');
        this.noResultsMessage = document.getElementById('noResults');
        this.resultsCount = document.getElementById('resultsCount');
        
        this.debounceTimeout = null;
        this.debounceDelay = 300; // milliseconds
        this.currentController = null;
        
        this.init();
    }
    
    init() {
        // On page load, check for query param and perform search if present
        const params = new URLSearchParams(window.location.search);
        const initialQuery = params.get('q') || '';
        if (initialQuery) {
            this.searchInput.value = initialQuery;
            this.performSearch(initialQuery);
        }
        this.searchInput.addEventListener('input', (e) => {
            const query = e.target.value;
            this.handleSearch(query);
            // Update the URL as the user types
            const url = new URL(window.location);
            if (query.trim()) {
                url.searchParams.set('q', query);
            } else {
                url.searchParams.delete('q');
            }
            window.history.replaceState({}, '', url);
        });
        
        // Handle enter key for immediate search
        this.searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                clearTimeout(this.debounceTimeout);
                this.performSearch(e.target.value);
            }
        });
    }
    
    handleSearch(query) {
        // Clear previous timeout
        clearTimeout(this.debounceTimeout);
        
        // Cancel previous request if still pending
        if (this.currentController) {
            this.currentController.abort();
        }
        
        // Clear results if query is empty
        if (!query.trim()) {
            this.clearResults();
            return;
        }
        
        // Set up debounced search
        this.debounceTimeout = setTimeout(() => {
            this.performSearch(query);
        }, this.debounceDelay);
    }
    
    async performSearch(query) {
        if (!query.trim()) return;
        
        // Show loading state
        this.showLoading();
        this.clearMessages();
        
        // Create new AbortController for this request
        this.currentController = new AbortController();
        const startTime = performance.now();
        
        try {
            const response = await fetch(`/search?q=${encodeURIComponent(query)}`, {
                method: 'GET',
                headers: {
                    'Content-Type': 'application/json',
                },
                signal: this.currentController.signal
            });
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            
            const data = await response.json();
            const elapsed = Math.round(performance.now() - startTime);
            this.displayResults(data, query, elapsed);
            
        } catch (error) {
            if (error.name === 'AbortError') {
                // Request was cancelled, ignore
                return;
            }
            
            console.error('Search error:', error);
            this.showError(`Failed to search: ${error.message}`);
        } finally {
            this.hideLoading();
            this.currentController = null;
        }
    }
    
    displayResults(data, query, elapsed) {
        // Assume the API returns an array of cards or an object with a cards array
        const cards = Array.isArray(data) ? data : data.cards || [];
        const totalCards = data.total_cards || cards.length;

        if (cards.length === 0) {
            this.showNoResults();
            return;
        }

        this.showResultsCount(totalCards, query, elapsed);

        this.resultsContainer.innerHTML = cards.map(card => 
            this.createCardHTML(card)
        ).join('');
    }
    
    createCardHTML(card) {
        return `
            <div class="card-item" onclick="cardSearch.selectCard('${card.id || card.name}')">
                ${card.image ? `<img class="card-image" src="${this.escapeHtml(card.image)}" alt="${this.escapeHtml(card.name || 'Card image')}" />` : ''}
                <div class="card-name">${this.escapeHtml(card.name || 'Unknown Card')}</div>
                ${card.mana_cost ? `<div class="card-mana">${this.escapeHtml(card.mana_cost)}</div>` : ''}
                ${card.type_line ? `<div class="card-type">${this.escapeHtml(card.type_line)}</div>` : ''}
                ${card.oracle_text ? `<div class="card-text">${this.escapeHtml(card.oracle_text).substring(0, 200)}${card.oracle_text.length > 200 ? '...' : ''}</div>` : ''}
                ${card.set_name ? `<div class="card-set">${this.escapeHtml(card.set_name)}</div>` : ''}
            </div>
        `;
    }
    
    selectCard(cardId) {
        console.log('Selected card:', cardId);
        // Add your card selection logic here
        // For example, you could open a modal, navigate to a detail page, etc.
    }
    
    showLoading() {
        this.loadingIndicator.style.display = 'block';
    }
    
    hideLoading() {
        this.loadingIndicator.style.display = 'none';
    }
    
    showError(message) {
        this.errorMessage.textContent = message;
        this.errorMessage.style.display = 'block';
        this.resultsContainer.innerHTML = '';
        this.resultsCount.style.display = 'none';
    }
    
    showNoResults() {
        this.noResultsMessage.style.display = 'block';
        this.resultsContainer.innerHTML = '';
        this.resultsCount.style.display = 'none';
    }
    
    showResultsCount(count, query, elapsed) {
        let msg = `Found ${count} card${count !== 1 ? 's' : ''} matching "${query}"`;
        if (typeof elapsed === 'number') {
            msg += ` (completed in ${elapsed}ms)`;
        }
        this.resultsCount.textContent = msg;
        this.resultsCount.style.display = 'block';
    }
    
    clearResults() {
        this.resultsContainer.innerHTML = '';
        this.clearMessages();
    }
    
    clearMessages() {
        this.errorMessage.style.display = 'none';
        this.noResultsMessage.style.display = 'none';
        this.resultsCount.style.display = 'none';
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize the search when the page loads
const cardSearch = new CardSearch();
