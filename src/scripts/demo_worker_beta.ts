const steps = [true, false, true];

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

console.log('beta standalone script started');

for (let index = 0; index < steps.length; index += 1) {
  console.log(`beta step ${index + 1}: switchOn=${steps[index]}`);
  await sleep(1500);
}

console.log('beta standalone script finished');

export {};
