// js/ui.js
import { Elements, WINNER_DISPLAY_DURATION } from './config.js';
import { createCardElement, addLogMessage } from './utils.js';
import * as state from './state.js'; // Import state for playerMap etc.
import { disableAllActions } from './actions.js'; // Import action functions

/** Clears player areas, community cards, and resets pot display */
export function clearTable() {
    Elements.pokerTable.querySelectorAll('.player-area').forEach(area => area.remove());
    Elements.communityCardArea.innerHTML = '<span class="waiting-message">Disconnected</span>';
    Elements.potArea.textContent = 'Pot: $?';
    hideWinnerDisplay();
}

/** Updates the entire game UI based on the state received from the server */
export function updateUI(serverState) {
    console.log("Updating UI with state:", serverState);
    state.setPlayerMap(serverState.players || {}); // Update local player map via state module

    Elements.pokerTable.querySelectorAll('.player-area').forEach(area => area.remove());

    const playerIds = Object.keys(state.playerMap);
    const myIdIndex = playerIds.findIndex(id => parseInt(id, 10) === state.myPlayerId);

    let displayOrderIds = [];
    if (myIdIndex !== -1) {
        displayOrderIds = [...playerIds.slice(myIdIndex), ...playerIds.slice(0, myIdIndex)];
    } else {
        displayOrderIds = playerIds;
    }

    displayOrderIds.forEach((playerId, displayIndex) => {
        const player = state.playerMap[playerId];
        if (player) {
            createPlayerArea(player, displayIndex, serverState.current_player_id, serverState.dealer_id);
        }
    });

    updateCommunityCards(serverState.community_cards, serverState.game_stage);
    Elements.potArea.textContent = `Pot: $${serverState.pot || 0}`;

    if (['showdown', 'hand_over'].includes(serverState.game_stage) && Elements.actionArea.style.display === 'flex') {
        disableAllActions(); // Ensure actions hidden if game state dictates
    }
}

/** Creates and appends a player area div to the table */
function createPlayerArea(playerData, displayIndex, currentPlayerId, dealerId) {
    const area = document.createElement('div');
    area.id = `player-area-${playerData.id}`;
    area.className = `player-area player-pos-${displayIndex}`;

    const nameDiv = document.createElement('div');
    nameDiv.className = 'player-name';
    const displayName = playerData.name || `Player ${playerData.id}`;
    nameDiv.textContent = displayName + (playerData.id === state.myPlayerId ? ' (You)' : '');
    nameDiv.title = displayName;
    area.appendChild(nameDiv);

    const chipsDiv = document.createElement('div');
    chipsDiv.className = 'player-chips';
    chipsDiv.textContent = `$${playerData.stack ?? '?'}`;
    area.appendChild(chipsDiv);

    const cardsDiv = document.createElement('div');
    cardsDiv.className = 'player-cards';
    if (playerData.hand && playerData.hand.length > 0) {
        playerData.hand.forEach(cardStr => {
            cardsDiv.appendChild(createCardElement(cardStr, cardStr === '??'));
        });
    }
    area.appendChild(cardsDiv);

    const statusDiv = document.createElement('div');
    statusDiv.className = 'player-status';
    statusDiv.textContent = getPlayerStatusText(playerData);
    area.appendChild(statusDiv);

    if (playerData.id === parseInt(currentPlayerId, 10)) area.classList.add('player-current');
    if (playerData.id === parseInt(dealerId, 10)) area.classList.add('player-dealer');
    if (playerData.status === 'folded') area.classList.add('folded');
    else area.classList.remove('folded');

    Elements.pokerTable.appendChild(area);
}

/** Determines the status text for a player */
function getPlayerStatusText(playerData) {
    switch (playerData.status) {
        case 'folded': return 'Folded';
        case 'all-in': return `All-in ($${playerData.current_bet || 0})`;
        default:
            if (playerData.current_bet > 0) return `Bet: $${playerData.current_bet}`;
            if (playerData.last_action && playerData.last_action !== 'blind' && playerData.last_action !== 'ante') {
                 return playerData.last_action.charAt(0).toUpperCase() + playerData.last_action.slice(1);
            }
            return '';
    }
}

/** Updates the community card display area */
function updateCommunityCards(cards, stage) {
     Elements.communityCardArea.innerHTML = '';
    if (cards && cards.length > 0) {
        cards.forEach(cardStr => Elements.communityCardArea.appendChild(createCardElement(cardStr)));
    } else if (stage && !['idle', 'starting', 'hand_over'].includes(stage)) {
        const stageText = stage.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase());
        Elements.communityCardArea.innerHTML = `<span class="waiting-message">Waiting for ${stageText}...</span>`;
    } else {
        Elements.communityCardArea.innerHTML = '<span class="waiting-message">Waiting for deal...</span>';
    }
}

/** Handles the display of revealed cards during showdown */
export function handleShowdownReveal(payload) {
     addLogMessage(`<strong>Game:</strong> --- Showdown ---`, "game");
      if (payload.allHands) {
         Object.entries(payload.allHands).forEach(([playerId, hand]) => {
             const area = document.getElementById(`player-area-${playerId}`);
             if (area) {
                 const cardsContainer = area.querySelector('.player-cards');
                 const statusContainer = area.querySelector('.player-status');
                 if (cardsContainer) {
                    cardsContainer.innerHTML = '';
                    if (hand && hand.length > 0) {
                         hand.forEach(cardStr => cardsContainer.appendChild(createCardElement(cardStr)));
                    } else {
                        cardsContainer.innerHTML = '<span class="waiting-message">- Folded -</span>';
                    }
                 }
                 if(statusContainer && payload.handRanks && payload.handRanks[playerId]) {
                    statusContainer.textContent = payload.handRanks[playerId];
                 }
                 area.classList.remove('folded');
             } else {
                 console.warn(`Showdown reveal: Player area not found for ID ${playerId}`);
             }
         });
     } else {
        console.warn("Showdown message received without 'allHands' data.");
        addLogMessage("<strong>System:</strong> Showdown occurred, but card data missing.", "system");
     }
     disableAllActions();
}

/** Handles the display of pot winner information */
 export function handlePotAwarded(payload) {
     console.log("Pot awarded payload:", payload);
     if (payload.winners && payload.winners.length > 0) {
         showWinnerDisplay(payload.winners, payload.isUncontested);
         payload.winners.forEach(winner => {
              const winnerName = winner.playerName || state.playerMap[winner.playerId]?.name || `Player ${winner.playerId}`;
              let winMessage = `<strong>${winnerName}</strong> wins $${winner.amount}`;
              if (!payload.isUncontested && winner.handRank) {
                  winMessage += ` with ${winner.handRank}`;
                  if(winner.winningHand && winner.winningHand.length > 0) {
                     const actualCards = winner.winningHand.filter(c => c !== '??');
                     if(actualCards.length > 0) winMessage += `: ${actualCards.join(', ')}`;
                  }
              } else if (payload.isUncontested) {
                 winMessage += ` (Uncontested)`;
              }
              addLogMessage(winMessage, "game");
         });
     } else {
         addLogMessage("<strong>Game:</strong> Pot awarded, but no winner information received.", "system");
     }
     addLogMessage(`<strong>Game:</strong> --- Hand Over ---`, "game");
     disableAllActions();
     state.setCurrentTurnOptions(null);
 }

/** Shows the winner display element with winner details */
 export function showWinnerDisplay(winnersData, isUncontested) {
    if (!Elements.winnerDisplay) return;
    let htmlContent = '';
     if (winnersData && winnersData.length > 0) {
         if (winnersData.length > 1) htmlContent = '<p>Split Pot!</p>';
         winnersData.forEach(winner => {
             const winnerName = winner.playerName || state.playerMap[winner.playerId]?.name || `Player ${winner.playerId}`;
             htmlContent += `<p>${winnerName} wins $${winner.amount}`;
             if (!isUncontested && winner.handRank) htmlContent += ` <span class="hand-rank">(${winner.handRank})</span>`;
             htmlContent += `</p>`;
         });
     } else {
         htmlContent = '<p>Error: No winner info available</p>';
     }
     Elements.winnerDisplay.innerHTML = htmlContent;
     Elements.winnerDisplay.style.display = 'block';
     state.clearWinnerDisplayTimeout(); // Clear previous timeout
     const timeoutId = setTimeout(hideWinnerDisplay, WINNER_DISPLAY_DURATION);
     state.setWinnerDisplayTimeout(timeoutId); // Store new timeout ID
 }

/** Hides the winner display element */
 export function hideWinnerDisplay() {
     if (Elements.winnerDisplay) Elements.winnerDisplay.style.display = 'none';
     state.clearWinnerDisplayTimeout(); // Clear timeout when hiding manually or automatically
 }