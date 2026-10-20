export function resolveStrategyDate(globalDate: string, availableDates: string[]): string {
  const requestedDate = globalDate.trim();
  return requestedDate || availableDates[0] || '';
}

export function strategyDateOptions(globalDate: string, availableDates: string[]): string[] {
  const requestedDate = globalDate.trim();
  if (!requestedDate || availableDates.includes(requestedDate)) {
    return availableDates;
  }
  return [requestedDate, ...availableDates];
}
