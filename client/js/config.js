// js/config.js

export const WS_URL = `ws://${window.location.hostname || 'localhost'}:8765`;
export const WINNER_DISPLAY_DURATION = 5000; // ms
export const MAX_NAME_LENGTH = 15;

// Export DOM element references
export const Elements = {
    nameModal: document.getElementById('name-modal'),
    nameInput: document.getElementById('name-input'),
    nameSubmit: document.getElementById('name-submit'),
    pokerTable: document.getElementById('poker-table'),
    communityCardArea: document.getElementById('community-cards'),
    potArea: document.getElementById('pot-area'),
    actionArea: document.getElementById('action-area'),
    messageLog: document.getElementById('message-log'),
    foldButton: document.getElementById('fold-button'),
    checkButton: document.getElementById('check-button'),
    callButton: document.getElementById('call-button'),
    betButton: document.getElementById('bet-button'),
    raiseButton: document.getElementById('raise-button'),
    betSliderContainer: document.getElementById('bet-slider-container'),
    betSlider: document.getElementById('bet-slider'),
    betAmountLabel: document.getElementById('bet-amount-label'),
    winnerDisplay: document.getElementById('winner-display'),
};