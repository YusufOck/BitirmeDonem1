import React from 'react';
import CourierCard from './CourierCard';
import './CourierList.css';

export default function CourierList({ couriers, selectedCourierId, onSelectCourier, pendingSuggestions = {} }) {
  if (!couriers || couriers.length === 0) {
    return <div className="no-couriers text-center">No active couriers found.</div>;
  }

  // Sort couriers so those with suggestions are at the top
  const sortedCouriers = [...couriers].sort((a, b) => {
    const aHas = !!pendingSuggestions[a.id];
    const bHas = !!pendingSuggestions[b.id];
    if (aHas && !bHas) return -1;
    if (!aHas && bHas) return 1;
    return a.id - b.id; // stable sort
  });

  return (
    <div className="courier-list-grid">
      {sortedCouriers.map(courier => (
        <CourierCard 
          key={courier.id} 
          courier={courier} 
          isSelected={selectedCourierId === courier.id} 
          onSelect={() => onSelectCourier(courier.id)}
          hasSuggestion={!!pendingSuggestions[courier.id]}
        />
      ))}
    </div>
  );
}
