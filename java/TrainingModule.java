import java.io.*;

public class TrainingModule {
    // Simple simulator: reads each file path from args, 'trains' by counting lines
    public static void main(String[] args) {
        if (args.length == 0) {
            System.out.println("Uso: java TrainingModule <file1> <file2> ...");
            System.exit(0);
        }
        for (String path : args) {
            File f = new File(path);
            if (!f.exists()) {
                System.out.println("No existe: " + path);
                continue;
            }
            try (BufferedReader br = new BufferedReader(new FileReader(f))) {
                int lines = 0;
                while (br.readLine() != null) lines++;
                // Simulate training time proportional to lines
                long sleep = Math.min(2000, lines * 5);
                System.out.println("Training on " + path + ": lines=" + lines + " sleeping=" + sleep + "ms");
                Thread.sleep(sleep);
                System.out.println("Finished training " + path);
            } catch (Exception e) {
                System.err.println("Error processing " + path + ": " + e);
            }
        }
    }
}
