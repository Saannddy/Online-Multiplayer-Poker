// js/utils.js
import { Elements } from './config.js';

/** Adds a message to the log area */
export function addLogMessage(htmlMessage, type = "system") {
    const p = document.createElement('p');
    p.className = `${type}-message`;
    p.innerHTML = `[${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}] ${htmlMessage}`;
    Elements.messageLog.appendChild(p);
    Elements.messageLog.scrollTop = Elements.messageLog.scrollHeight;
}

/** Creates a card div element */
export function createCardElement(cardStr, isBack = false) {
    const cardDiv = document.createElement('div');
    cardDiv.className = 'card';

    if (isBack || !cardStr || cardStr === "??") {
        cardDiv.classList.add('card-back');
        cardDiv.innerHTML = '&nbsp;';
        return cardDiv;
    }
    if (typeof cardStr !== 'string' || cardStr.length < 2) {
        console.warn("Invalid card string:", cardStr);
        cardDiv.textContent = '?';
        return cardDiv;
    }
    const rank = cardStr.slice(0, -1);
    const suit = cardStr.slice(-1);
    let suitClass = '';
    switch (suit) {
        case '♥': suitClass = 'heart'; break;
        case '♦': suitClass = 'diamond'; break;
        case '♣': suitClass = 'club'; break;
        case '♠': suitClass = 'spade'; break;
        default: console.warn("Unknown suit:", suit);
    }
    cardDiv.textContent = rank + suit;
    if (suitClass) cardDiv.classList.add(suitClass);
    return cardDiv;
}