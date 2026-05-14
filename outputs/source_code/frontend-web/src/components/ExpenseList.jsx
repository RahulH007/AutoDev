import React from 'react';
import api from '../services/api';

export default function ExpenseList({ expenses, onDelete }) {
  const handleDelete = async (id) => {
    if (window.confirm('Are you sure?')) {
      await api.delete(`/expenses/${id}`);
      onDelete();
    }
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left">
        <thead>
          <tr className="border-b">
            <th className="py-2">Date</th>
            <th className="py-2">Description</th>
            <th className="py-2">Amount</th>
            <th className="py-2">Action</th>
          </tr>
        </thead>
        <tbody>
          {expenses.map(exp => (
            <tr key={exp.id} className="border-b">
              <td className="py-2">{exp.transaction_date}</td>
              <td className="py-2">{exp.description}</td>
              <td className="py-2 font-semibold">${exp.amount.toFixed(2)}</td>
              <td className="py-2">
                <button onClick={() => handleDelete(exp.id)} className="text-red-500">Delete</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}