class ESI:
    def __init__(self):
        self.wait_times = {1: [], 2: [], 3: [], 4: [], 5: []} # creates a dictionary of lists to store wait times for each ESI level

    def record_wait_time(self, esi_level, wait_time):
        self.wait_times[esi_level].append(wait_time) # just taking wait time measured and storing in list for the patient's ESI level

    def report(self):
        for level in self.wait_times:
            if self.wait_times[level]: # if there are wait times recorded for this ESI level
                count = len(self.wait_times[level]) # counts how many patients are in this ESI level
                average_wait = sum(self.wait_times[level]) / len(self.wait_times[level]) # calculate average wait time
                print("ESI Level: ", level, "Patients: ", count, "Average Wait Time: ", round(average_wait, 2), "minutes") # print average wait time for this ESI level